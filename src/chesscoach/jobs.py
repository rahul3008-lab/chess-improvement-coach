from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

import chess
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from chesscoach.engines import Stockfish, cache_key
from chesscoach.pgn import digest, parse_games
from chesscoach.storage.models import (
    Account,
    AccountGame,
    AnalysisJob,
    Game,
    MoveEvaluation,
    PositionCache,
    now,
)


def game_ids_for_player(player_id):
    return select(AccountGame.game_id).join(Account).where(Account.player_id == player_id)


def enqueue(db, player_id, config, limit=None, priority=0, retry=False, game_ids=None):
    fingerprint = digest(config)
    count = 0
    with db.session() as session:
        query = select(Game).where(Game.id.in_(game_ids_for_player(player_id)))
        if game_ids is not None:
            query = query.where(Game.id.in_(game_ids))
        games = session.scalars(query.order_by(Game.played_at.desc(), Game.id.desc())).all()
        for game in games:
            job = session.scalar(
                select(AnalysisJob).where(
                    AnalysisJob.game_id == game.id,
                    AnalysisJob.config_hash == fingerprint,
                    AnalysisJob.content_hash == game.content_hash,
                )
            )
            if job is None:
                session.add(
                    AnalysisJob(
                        game_id=game.id,
                        config_hash=fingerprint,
                        content_hash=game.content_hash,
                        config=config,
                        priority=priority,
                    )
                )
                count += 1
            elif retry and job.status in {"failed", "cancelled"}:
                job.status, job.error, job.priority = "pending", None, priority
                count += 1
            if limit is not None and count >= limit:
                break
    return count, fingerprint


def recover_expired(db):
    with db.session() as session:
        result = session.execute(
            update(AnalysisJob)
            .where(AnalysisJob.status == "running", AnalysisJob.lease_until < now())
            .values(
                status="pending",
                lease_token=None,
                lease_until=None,
                error="Worker lease expired; recovered for retry.",
            )
        )
        return result.rowcount


def claim(db, lease_seconds, player_id=None, config_hash=None, game_ids=None):
    # Compare-and-swap keeps claims safe across threads AND independent worker processes.
    for _ in range(30):
        with db.session() as session:
            query = (
                select(AnalysisJob.id)
                .join(Game)
                .where(AnalysisJob.status == "pending", AnalysisJob.content_hash == Game.content_hash)
            )
            if player_id is not None:
                query = query.where(AnalysisJob.game_id.in_(game_ids_for_player(player_id)))
            if config_hash:
                query = query.where(AnalysisJob.config_hash == config_hash)
            if game_ids is not None:
                query = query.where(AnalysisJob.game_id.in_(game_ids))
            job_id = session.scalar(query.order_by(AnalysisJob.priority.desc(), AnalysisJob.id).limit(1))
            if job_id is None:
                return None
            token = uuid.uuid4().hex
            result = session.execute(
                update(AnalysisJob)
                .where(AnalysisJob.id == job_id, AnalysisJob.status == "pending")
                .values(
                    status="running",
                    lease_token=token,
                    lease_until=now() + timedelta(seconds=lease_seconds),
                    attempts=AnalysisJob.attempts + 1,
                )
            )
            if result.rowcount:
                return job_id, token
    return None


def phase(board):
    material = sum(
        len(board.pieces(piece, color))
        for piece in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT)
        for color in chess.COLORS
    )
    if material <= 4:
        return "endgame"
    return "opening" if board.fullmove_number <= 12 else "middlegame"


def label_loss(loss):
    return "blunder" if loss >= 200 else "mistake" if loss >= 100 else "inaccuracy" if loss >= 50 else "sound"


class LeaseLost(Exception):
    pass


def process_job(db, job_id, token, engine, settings, stop):
    with db.session() as session:
        job = session.get(AnalysisJob, job_id)
        game = session.get(Game, job.game_id)
        pgn, content, fingerprint = game.pgn, job.content_hash, job.config_hash
    parsed = next(parse_games(pgn))
    board = parsed.board()
    rows = []

    def heartbeat():
        if stop.is_set():
            raise InterruptedError("Worker shutdown requested")
        with db.session() as session:
            changed = session.execute(
                update(AnalysisJob)
                .where(
                    AnalysisJob.id == job_id,
                    AnalysisJob.status == "running",
                    AnalysisJob.lease_token == token,
                )
                .values(lease_until=now() + timedelta(seconds=settings.analysis.lease_seconds))
            )
            if not changed.rowcount:
                raise LeaseLost("Job cancelled or claimed by another worker")

    def evaluate(position):
        heartbeat()
        key = cache_key(position, fingerprint)
        if settings.analysis.enable_cache:
            with db.session() as session:
                cached = session.get(PositionCache, key)
                if cached:
                    return cached.result
        value = engine.evaluate(position)
        if settings.analysis.enable_cache:
            try:
                with db.session() as session:
                    if session.get(PositionCache, key) is None:
                        session.add(PositionCache(key=key, result=value))
            except IntegrityError:
                pass  # Another worker populated precisely this cache entry.
        return value

    before = evaluate(board)
    for ply, node in enumerate(parsed.mainline(), 1):
        old_fen, old_phase, turn = board.fen(), phase(board), board.turn
        board.push(node.move)
        after = evaluate(board)
        loss = max(0, (before["cp"] - after["cp"]) * (1 if turn == chess.WHITE else -1))
        if before["pv"] and before["pv"][0] == node.move.uci():
            loss = 0  # Search noise on the selected best move is not a player error.
        rows.append(
            MoveEvaluation(
                job_id=job_id,
                ply=ply,
                fen=old_fen,
                played_uci=node.move.uci(),
                best_uci=before["pv"][0] if before["pv"] else None,
                cp_before=before["cp"],
                cp_after=after["cp"],
                mate_before=before.get("mate"),
                mate_after=after.get("mate"),
                loss=loss,
                label=label_loss(loss),
                phase=old_phase,
                pv=before["pv"],
                alternatives=before.get("lines", []),
                depth=before.get("depth", 0),
                clock=node.clock(),
            )
        )
        before = after
    heartbeat()
    with db.session() as session:
        game = session.get(Game, job.game_id)
        if game.content_hash != content:
            raise ValueError("Game changed during analysis; queue its new content version.")
        changed = session.execute(
            update(AnalysisJob)
            .where(
                AnalysisJob.id == job_id, AnalysisJob.lease_token == token, AnalysisJob.status == "running"
            )
            .values(status="completed", finished_at=now(), lease_token=None, lease_until=None, error=None)
        )
        if not changed.rowcount:
            raise LeaseLost()
        session.execute(delete(MoveEvaluation).where(MoveEvaluation.job_id == job_id))
        session.add_all(rows)


@dataclass
class WorkerResult:
    completed: int = 0
    failed: int = 0
    recovered: int = 0


def run_workers(
    db,
    settings,
    player_id=None,
    config_hash=None,
    max_jobs=None,
    workers=None,
    factory=None,
    on_progress=None,
    game_ids=None,
):
    result = WorkerResult(recovered=recover_expired(db))
    stop = threading.Event()
    lock = threading.Lock()
    claimed = 0

    def worker():
        nonlocal claimed
        while not stop.is_set():
            with lock:
                if max_jobs is not None and claimed >= max_jobs:
                    return
                item = claim(db, settings.analysis.lease_seconds, player_id, config_hash, game_ids)
                if item is None:
                    return
                claimed += 1
            job_id, token = item
            engine = None
            keeper_stop = threading.Event()

            def renew_lease():
                while not keeper_stop.wait(min(30, settings.analysis.lease_seconds / 3)):
                    with db.session() as session:
                        changed = session.execute(
                            update(AnalysisJob)
                            .where(
                                AnalysisJob.id == job_id,
                                AnalysisJob.status == "running",
                                AnalysisJob.lease_token == token,
                            )
                            .values(lease_until=now() + timedelta(seconds=settings.analysis.lease_seconds))
                        )
                        if not changed.rowcount:
                            return

            keeper = threading.Thread(target=renew_lease, daemon=True)
            keeper.start()
            try:
                with db.session() as session:
                    config = session.get(AnalysisJob, job_id).config
                if factory:
                    engine = factory(config)
                else:
                    # Every job runs with the configuration fingerprint it was queued with.
                    job_settings = settings.model_copy(deep=True)
                    for key in ("threads", "hash_mb", "multipv"):
                        setattr(job_settings.engine, key, config[key])
                    job_settings.analysis.seconds = config["seconds"]
                    engine = Stockfish(job_settings, depth=config["depth"])
                if digest(engine.identity) != digest(config):
                    raise ValueError("Engine version/configuration changed. Queue a new analysis instead.")
                process_job(db, job_id, token, engine, settings, stop)
                with lock:
                    result.completed += 1
            except LeaseLost:
                pass
            except BaseException as error:
                with db.session() as session:
                    session.execute(
                        update(AnalysisJob)
                        .where(
                            AnalysisJob.id == job_id,
                            AnalysisJob.lease_token == token,
                            AnalysisJob.status == "running",
                        )
                        .values(
                            status="pending" if stop.is_set() else "failed",
                            error=str(error)[:2000],
                            lease_token=None,
                            lease_until=None,
                        )
                    )
                with lock:
                    result.failed += 1
            finally:
                keeper_stop.set()
                keeper.join()
                if engine:
                    try:
                        engine.close()
                    except (OSError, chess.engine.EngineError):
                        pass
            if on_progress:
                on_progress(job_id, result)

    pool = ThreadPoolExecutor(max_workers=workers or settings.analysis.workers)
    futures = [pool.submit(worker) for _ in range(workers or settings.analysis.workers)]
    try:
        for future in futures:
            future.result()
    except KeyboardInterrupt:
        stop.set()
    finally:
        pool.shutdown(wait=True)
    return result
