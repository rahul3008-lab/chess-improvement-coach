import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import chess
import pytest
from conftest import FAKE_CONFIG, FakeEngine
from sqlalchemy import func, select

from chesscoach.engines import Stockfish, cache_key
from chesscoach.jobs import claim, enqueue, recover_expired, run_workers
from chesscoach.storage.models import AnalysisJob, Game, MoveEvaluation, PositionCache, now


def test_queue_analyse_cache_and_configuration_version(db, imported, settings):
    queued, fingerprint = enqueue(db, imported, FAKE_CONFIG)
    assert queued == 2
    result = run_workers(db, settings, imported, fingerprint, factory=lambda _: FakeEngine(), workers=2)
    assert result.completed == 2 and result.failed == 0
    assert enqueue(db, imported, FAKE_CONFIG)[0] == 0
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(MoveEvaluation)) == 11
        assert session.scalar(select(func.count()).select_from(PositionCache)) > 0
        assert all(j.status == "completed" for j in session.scalars(select(AnalysisJob)))
    updated = {**FAKE_CONFIG, "engine": {"name": "FakeEngine v2"}}
    assert enqueue(db, imported, updated)[0] == 2
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(MoveEvaluation)) == 11


def test_content_change_invalidates_analysis_without_deleting_old(db, imported, settings):
    enqueue(db, imported, FAKE_CONFIG)
    run_workers(db, settings, imported, factory=lambda _: FakeEngine())
    with db.session() as session:
        session.scalars(select(Game)).first().content_hash = "new-content"
    assert enqueue(db, imported, FAKE_CONFIG)[0] == 1
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(AnalysisJob)) == 3
        assert session.scalar(select(func.count()).select_from(MoveEvaluation)) == 11


def test_atomic_claim_and_expired_recovery(db, imported):
    enqueue(db, imported, FAKE_CONFIG)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: claim(db, 900), range(4)))
    actual = [item for item in claims if item]
    assert len(actual) == 2 and len({item[0] for item in actual}) == 2
    assert recover_expired(db) == 0
    with db.session() as session:
        session.get(AnalysisJob, actual[0][0]).lease_until = now() - timedelta(seconds=1)
    assert recover_expired(db) == 1
    assert claim(db, 900)[0] == actual[0][0]


def test_selected_batch_does_not_queue_or_process_other_games(db, imported, settings):
    with db.session() as session:
        ids = list(session.scalars(select(Game.id).order_by(Game.played_at.desc(), Game.id.desc())))
    selected = [ids[0]]
    queued, fingerprint = enqueue(db, imported, FAKE_CONFIG, game_ids=selected)
    assert queued == 1
    assert enqueue(db, imported, FAKE_CONFIG, game_ids=selected)[0] == 0
    assert enqueue(db, imported, FAKE_CONFIG)[0] == 1
    result = run_workers(
        db, settings, imported, fingerprint, game_ids=selected, factory=lambda _: FakeEngine()
    )
    assert result.completed == 1
    with db.session() as session:
        assert session.scalar(select(AnalysisJob.status).where(AnalysisJob.game_id == ids[1])) == "pending"
        assert session.scalar(select(AnalysisJob.status).where(AnalysisJob.game_id == ids[0])) == "completed"


def test_failed_job_can_retry_and_wrong_version_is_rejected(db, imported, settings):
    enqueue(db, imported, FAKE_CONFIG, limit=1)

    class Changed(FakeEngine):
        identity = {**FAKE_CONFIG, "engine": {"name": "changed"}}

    result = run_workers(db, settings, imported, factory=lambda _: Changed())
    assert result.failed == 1
    with db.session() as session:
        job = session.scalars(select(AnalysisJob)).first()
        assert "version/configuration changed" in job.error
    assert enqueue(db, imported, FAKE_CONFIG, limit=1, retry=True)[0] == 1
    assert run_workers(db, settings, imported, factory=lambda _: FakeEngine()).completed == 1


def test_cache_reused_after_retry(db, imported, settings):
    FakeEngine.calls = 0
    enqueue(db, imported, FAKE_CONFIG, limit=1)
    run_workers(db, settings, imported, factory=lambda _: FakeEngine())
    previous = FakeEngine.calls
    with db.session() as session:
        session.scalars(select(AnalysisJob)).first().status = "pending"
    run_workers(db, settings, imported, factory=lambda _: FakeEngine())
    assert FakeEngine.calls == previous


def test_history_aware_cache_key():
    board = chess.Board()
    for move in ["g1f3", "g8f6", "f3g1", "f6g8"]:
        board.push_uci(move)
    same_fen = chess.Board(board.fen())
    assert board.fen() == same_fen.fen()
    assert cache_key(board, "config") != cache_key(same_fen, "config")


@pytest.mark.engine
@pytest.mark.skipif(shutil.which("stockfish") is None, reason="Stockfish not installed")
def test_real_stockfish_mate_and_worker(db, imported, settings):
    settings.engine.threads = 1
    settings.engine.hash_mb = 16
    settings.analysis.seconds = 0.02
    with Stockfish(settings, depth=5) as engine:
        board = chess.Board()
        for move in ["f2f3", "e7e5", "g2g4"]:
            board.push_uci(move)
        value = engine.evaluate(board)
        assert value["pv"][0] == "d8h4" and value["mate"] == -1
        _, fingerprint = enqueue(db, imported, engine.identity, limit=1)
    result = run_workers(db, settings, imported, fingerprint, max_jobs=1)
    assert result.completed == 1 and result.failed == 0


def test_cancellation_rejects_old_worker_commit(db, imported, settings):
    enqueue(db, imported, FAKE_CONFIG, limit=1)

    class Cancelling(FakeEngine):
        def evaluate(self, board):
            with db.session() as session:
                job = session.scalars(select(AnalysisJob)).first()
                job.status, job.lease_token = "cancelled", None
            return super().evaluate(board)

    result = run_workers(db, settings, imported, factory=lambda _: Cancelling())
    assert result.completed == 0
    with db.session() as session:
        assert session.scalars(select(AnalysisJob)).first().status == "cancelled"
        assert session.scalar(select(func.count()).select_from(MoveEvaluation)) == 0
