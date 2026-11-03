import chess
import pytest
from conftest import FAKE_CONFIG, FakeEngine
from sqlalchemy import func, select

from chesscoach.identity import resolve_account
from chesscoach.jobs import enqueue, run_workers
from chesscoach.practice import (
    begin_session,
    generate,
    legal_move,
    next_exercises,
    record_attempt,
    validated_pv,
)
from chesscoach.reports import overview, period_report, refresh_coaching
from chesscoach.storage.models import (
    Attempt,
    Exercise,
    ProgressSnapshot,
    Recommendation,
    Weakness,
)


@pytest.fixture
def analysed(db, imported, settings):
    enqueue(db, imported, FAKE_CONFIG)
    run_workers(db, settings, imported, factory=lambda _: FakeEngine())
    refresh_coaching(db, imported)
    return imported


def test_player_dashboard_and_recommendations(db, analysed):
    result = overview(db, analysed)
    assert result["games"] == 2 and result["analysed_games"] == 2
    assert result["evaluated_moves"] == 5  # First game: 2 white moves; second game: 3 black moves.
    assert result["blunders"] > 0
    assert result["results"] == {"loss": 2}
    assert overview(db, analysed, color="white")["games"] == 1
    assert overview(db, analysed, opening="C20")["games"] == 1
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(Recommendation)) > 0
        assert session.scalar(select(func.count()).select_from(Weakness)) > 0
    refresh_coaching(db, analysed)
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(ProgressSnapshot)) == 1
    assert not period_report(db, analysed)["comparison_supported"]


def test_exercise_generation_grading_and_persistence(db, analysed):
    assert generate(db, analysed) > 0
    assert generate(db, analysed) == 0
    exercises = next_exercises(db, analysed)
    exercise = exercises[0]
    board = chess.Board(exercise.fen)
    assert chess.Move.from_uci(exercise.solution[0]) in board.legal_moves
    practice = begin_session(db, analysed, "mistakes")
    assert record_attempt(db, analysed, practice, exercise.id, [exercise.solution[0]], 5)
    with db.session() as session:
        stored = session.get(Exercise, exercise.id)
        assert stored.interval_days == 1 and stored.streak == 1
    assert overview(db, analysed)["practice_accuracy"] == 1
    assert record_attempt(db, analysed, practice, exercise.id, [exercise.solution[0]], 6, hints=1)
    with db.session() as session:
        assert session.get(Exercise, exercise.id).streak == 0
        assert session.scalar(select(func.count()).select_from(Attempt)) == 2
    assert overview(db, analysed)["unassisted_accuracy"] == 0.5


def test_attempts_cannot_cross_players(db, analysed):
    generate(db, analysed)
    exercise = next_exercises(db, analysed)[0]
    with db.session() as session:
        other = resolve_account(session, "local", "beta").player_id
    other_session = begin_session(db, other, "mistakes")
    with pytest.raises(ValueError, match="belong"):
        record_attempt(db, other, other_session, exercise.id, exercise.solution[:1], 2)
    assert overview(db, other)["practice_attempts"] == 0
    assert overview(db, other)["games"] == 0


def test_calculation_requires_full_requested_line(db, analysed):
    generate(db, analysed, mode="calculation")
    exercise = next_exercises(db, analysed, "calculation")[0]
    session_id = begin_session(db, analysed, "calculation")
    if len(exercise.solution) >= 5:
        assert not record_attempt(db, analysed, session_id, exercise.id, exercise.solution[:1], 1)
        assert record_attempt(db, analysed, session_id, exercise.id, exercise.solution[:5], 2)


@pytest.mark.parametrize(
    "fen,move",
    [
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "O-O"),
        ("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "a7a8n"),
        ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2", "e5d6"),
    ],
)
def test_special_move_legality(fen, move):
    board = chess.Board(fen)
    parsed = legal_move(board, move)
    assert parsed in board.legal_moves
    board.push(parsed)
    assert board.is_valid()


def test_invalid_move_and_pv():
    with pytest.raises(ValueError, match="Illegal"):
        legal_move(chess.Board(), "e2e5")
    assert validated_pv(chess.STARTING_FEN, ["e2e4", "e7e5", "e1e8"]) == ["e2e4", "e7e5"]


def test_completed_session_rejects_attempt(db, analysed):
    generate(db, analysed)
    exercise = next_exercises(db, analysed)[0]
    session_id = begin_session(db, analysed, "mistakes")
    from chesscoach.practice import finish_session

    finish_session(db, analysed, session_id)
    with pytest.raises(ValueError, match="no longer active"):
        record_attempt(db, analysed, session_id, exercise.id, exercise.solution[:1], 1)
