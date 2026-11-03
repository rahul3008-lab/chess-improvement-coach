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
    source_board,
    submit_answer,
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


def test_wrong_answer_resets_position_and_successful_retry_finishes_session(db, analysed):
    import time

    from chesscoach.storage.models import PracticeSession

    generate(db, analysed)
    exercise = next(e for e in next_exercises(db, analysed) if chess.Board(e.fen).legal_moves.count() > 1)
    initial = source_board(db, analysed, exercise.id)
    state = {
        "initial_board": initial,
        "board": initial.copy(),
        "started": time.monotonic(),
        "hints": 0,
        "submitted": False,
        "feedback": None,
        "session_id": begin_session(db, analysed, exercise.mode),
    }
    wrong = next(move.uci() for move in initial.legal_moves if move.uci() != exercise.solution[0])
    assert not submit_answer(db, analysed, exercise, state, [wrong])
    assert state["board"].fen() == initial.fen()
    assert not state["submitted"] and state["feedback"] == "wrong"
    assert exercise.id not in [item.id for item in next_exercises(db, analysed)]
    assert exercise.id in [item.id for item in next_exercises(db, analysed, include_id=exercise.id)]
    with db.session() as session:
        assert session.get(PracticeSession, state["session_id"]).finished_at is None
    assert submit_answer(db, analysed, exercise, state, exercise.solution[:1])
    assert state["submitted"] and state["feedback"] == "correct"
    assert state["board"].fen() != initial.fen()
    with db.session() as session:
        assert session.get(PracticeSession, state["session_id"]).finished_at is not None
        assert session.get(Exercise, exercise.id).streak == 0  # A retry is still scheduled for review.
        attempts = session.scalars(
            select(Attempt).where(Attempt.session_id == state["session_id"]).order_by(Attempt.id)
        ).all()
        assert [attempt.correct for attempt in attempts] == [False, True]


def test_practice_selection_does_not_load_full_analysis_history(db, analysed, monkeypatch):
    generate(db, analysed)

    def forbidden(*args, **kwargs):
        raise AssertionError("Practice loaded every game evaluation")

    monkeypatch.setattr("chesscoach.practice.evaluation_rows", forbidden)
    assert next_exercises(db, analysed)


def test_playground_keeps_wrong_puzzle_and_accepts_retry(db, analysed, monkeypatch, tmp_path):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("CHESSCOACH_DATABASE_URL", str(db.engine.url))
    monkeypatch.setenv("CHESSCOACH_DATA_DIR", str(tmp_path))
    generate(db, analysed)
    exercise = next_exercises(db, analysed)[0]
    initial = chess.Board(exercise.fen)
    wrong = next(move.uci() for move in initial.legal_moves if move.uci() != exercise.solution[0])
    events = [wrong]

    def board_event(board, key, *args, **kwargs):
        if key.startswith("exercise_board") and events:
            return chess.Move.from_uci(events.pop(0))
        return None

    monkeypatch.setattr("chesscoach.dashboard.widgets.board_widget", board_event)
    script = Path(__file__).parents[1] / "src/chesscoach/dashboard/app.py"
    view = AppTest.from_file(str(script)).run(timeout=30)
    view.sidebar.radio[0].set_value("Practice Playground").run(timeout=30)
    assert not view.exception
    assert any("Wrong answer. Try again." in item.value for item in view.error)
    position = next(item for item in view.selectbox if item.label == "Position")
    assert position.value == exercise.id
    state = view.session_state[f"practice_{analysed}_mistakes_{exercise.id}"]
    assert state["board"].fen() == exercise.fen and not state["submitted"]
    events.append(exercise.solution[0])
    view.run(timeout=30)
    assert not view.exception
    assert any("Correct!" in item.value for item in view.success)
    with db.session() as session:
        assert [a.correct for a in session.scalars(select(Attempt).order_by(Attempt.id))] == [False, True]
