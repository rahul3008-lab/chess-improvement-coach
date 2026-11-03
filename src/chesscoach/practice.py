from __future__ import annotations

import time
from datetime import timedelta

import chess
import chess.syzygy
from sqlalchemy import func, or_, select

from chesscoach.pgn import parse_games
from chesscoach.reports import evaluation_rows
from chesscoach.storage.models import (
    Account,
    AccountGame,
    AnalysisJob,
    Attempt,
    Exercise,
    Game,
    MoveEvaluation,
    PracticeSession,
    now,
)

MODES = {"mistakes", "tactics", "endgame", "critical", "opening", "calculation"}


def legal_move(board, move):
    try:
        parsed = chess.Move.from_uci(move.strip())
        if parsed not in board.legal_moves:
            raise ValueError("Illegal move.")
        return parsed
    except (ValueError, chess.InvalidMoveError):
        try:
            return board.parse_san(move.strip())
        except ValueError as error:
            raise ValueError("Illegal move. Use UCI (e2e4, a7a8q) or SAN (Nf3, O-O).") from error


def validated_pv(fen, pv):
    board = chess.Board(fen)
    if not board.is_valid():
        return []
    result = []
    for uci in pv:
        try:
            move = legal_move(board, uci)
        except ValueError:
            break
        result.append(move.uci())
        board.push(move)
    return result


def generate(db, player_id, mode="mistakes", limit=100):
    if mode not in MODES:
        raise ValueError(f"Unknown practice mode: {mode}")
    created = 0
    with db.session() as session:
        _, _, values = evaluation_rows(session, player_id)
        # Retain attempt history while hiding exercises from superseded game/analysis content.
        current_ids = {v.id for v, *_ in values}
        for exercise in session.scalars(
            select(Exercise).where(Exercise.player_id == player_id, Exercise.mode == mode)
        ):
            exercise.active = exercise.evaluation_id in current_ids
        for value, game, *_ in sorted(values, key=lambda row: row[0].loss, reverse=True):
            if value.loss < 100 or not value.best_uci or value.best_uci == value.played_uci:
                continue
            if mode == "endgame" and value.phase != "endgame":
                continue
            if mode == "opening" and value.phase != "opening":
                continue
            board = chess.Board(value.fen)
            best = legal_move(board, value.best_uci)
            # Observable tactical candidates; no unsupported pin/fork/strategy diagnosis.
            tactical = board.is_capture(best) or board.gives_check(best)
            if mode == "tactics" and not tactical:
                continue
            # Reject ambiguous positions if the engine's second line is within 50cp.
            lines = value.alternatives
            if len(lines) > 1 and abs(lines[0]["cp"] - lines[1]["cp"]) < 50:
                continue
            solution = validated_pv(value.fen, value.pv[:7])
            if not solution:
                continue
            exists = session.scalar(
                select(Exercise).where(
                    Exercise.player_id == player_id, Exercise.evaluation_id == value.id, Exercise.mode == mode
                )
            )
            if exists:
                continue
            session.add(
                Exercise(
                    player_id=player_id,
                    evaluation_id=value.id,
                    game_id=game.id,
                    mode=mode,
                    fen=value.fen,
                    solution=solution,
                    topic=value.phase,
                )
            )
            created += 1
            if created >= limit:
                break
    return created


def next_exercises(db, player_id, mode="mistakes", due_only=True, include_id=None):
    with db.session() as session:
        # Select current job IDs in SQL instead of loading every move and rebuilding every board.
        ranked = (
            select(
                AnalysisJob.id.label("id"),
                func.row_number()
                .over(
                    partition_by=AnalysisJob.game_id,
                    order_by=(AnalysisJob.finished_at.desc(), AnalysisJob.id.desc()),
                )
                .label("rank"),
            )
            .join(Game)
            .where(
                AnalysisJob.status == "completed",
                AnalysisJob.content_hash == Game.content_hash,
                Game.id.in_(select(AccountGame.game_id).join(Account).where(Account.player_id == player_id)),
            )
            .subquery()
        )
        current_jobs = select(ranked.c.id).where(ranked.c.rank == 1)
        query = select(Exercise).where(
            Exercise.player_id == player_id,
            Exercise.mode == mode,
            Exercise.active.is_(True),
            Exercise.evaluation_id.in_(
                select(MoveEvaluation.id).where(MoveEvaluation.job_id.in_(current_jobs))
            ),
        )
        if due_only:
            query = query.where(or_(Exercise.due_at <= now(), Exercise.id == include_id))
        return session.scalars(query.order_by(Exercise.due_at, Exercise.id)).all()


def begin_session(db, player_id, mode):
    with db.session() as session:
        item = PracticeSession(player_id=player_id, mode=mode)
        session.add(item)
        session.flush()
        return item.id


def finish_session(db, player_id, session_id):
    with db.session() as session:
        item = session.get(PracticeSession, session_id)
        if item is None or item.player_id != player_id:
            raise ValueError("Session does not belong to this player.")
        item.finished_at = now()


def record_attempt(db, player_id, session_id, exercise_id, moves, seconds, hints=0, spaced=True):
    if seconds < 0 or hints < 0 or not moves:
        raise ValueError("An attempt needs at least one move and nonnegative time/hints.")
    with db.session() as session:
        exercise, practice = session.get(Exercise, exercise_id), session.get(PracticeSession, session_id)
        if not exercise or not practice or exercise.player_id != player_id or practice.player_id != player_id:
            raise ValueError("Exercise and session must belong to the selected player.")
        if practice.finished_at or practice.mode != exercise.mode or not exercise.active:
            raise ValueError("This session/exercise is no longer active or has a different mode.")
        board = chess.Board(exercise.fen)
        normalised = []
        for move in moves:
            parsed = legal_move(board, move)
            normalised.append(parsed.uci())
            board.push(parsed)
        expected = exercise.solution
        required = min(len(expected), 5) if exercise.mode == "calculation" else 1
        correct = len(normalised) >= required and normalised == expected[: len(normalised)]
        session.add(
            Attempt(
                session_id=session_id,
                exercise_id=exercise_id,
                moves=normalised,
                correct=correct,
                seconds=seconds,
                hints=hints,
            )
        )
        previously_wrong = (
            session.scalar(
                select(Attempt.id)
                .where(
                    Attempt.session_id == session_id,
                    Attempt.exercise_id == exercise_id,
                    Attempt.correct.is_(False),
                )
                .limit(1)
            )
            is not None
        )
        if correct and hints == 0 and not previously_wrong:
            exercise.streak += 1
            exercise.interval_days = min(90, max(1, exercise.interval_days * 2))
        else:
            exercise.streak, exercise.interval_days = 0, 0
        exercise.due_at = now() + (
            timedelta(days=exercise.interval_days)
            if spaced and exercise.interval_days
            else timedelta(minutes=10)
        )
        return correct


def submit_answer(db, player_id, exercise, state, moves, spaced=True):
    """A wrong legal answer records an attempt but keeps the original puzzle ready to retry."""
    if state["submitted"]:
        raise ValueError("This exercise is already solved. Reset it to start another attempt.")
    correct = record_attempt(
        db,
        player_id,
        state["session_id"],
        exercise.id,
        moves,
        time.monotonic() - state["started"],
        state["hints"],
        spaced,
    )
    state["feedback"] = "correct" if correct else "wrong"
    state["submitted"] = correct
    state["board"] = state["initial_board"].copy()
    if correct:
        for value in moves:
            state["board"].push(legal_move(state["board"], value))
        finish_session(db, player_id, state["session_id"])
    else:
        state["started"] = time.monotonic()
    return correct


def source_board(db, player_id, exercise_id):
    """Rebuild game history, preserving repetition rights for engine continuation."""
    with db.session() as session:
        exercise = session.get(Exercise, exercise_id)
        if exercise is None or exercise.player_id != player_id:
            raise ValueError("Exercise does not belong to player.")
        game = session.get(Game, exercise.game_id)
        parsed = next(parse_games(game.pgn))
        board = parsed.board()
        if board.fen() == exercise.fen:
            return board
        for move in parsed.mainline_moves():
            board.push(move)
            if board.fen() == exercise.fen:
                return board
    raise ValueError("Exercise position is no longer present in the source game.")


def tablebase_moves(board, directory):
    """Optional local Syzygy guidance; caller supplies independently downloaded table files."""
    if not board.is_valid():
        raise ValueError("Invalid endgame position.")
    with chess.syzygy.open_tablebase(directory) as tablebase:
        ranked = []
        for move in board.legal_moves:
            child = board.copy()
            child.push(move)
            ranked.append(
                {"move": move.uci(), "wdl": -tablebase.probe_wdl(child), "dtz": -tablebase.probe_dtz(child)}
            )
        return sorted(ranked, key=lambda value: value["wdl"], reverse=True)
