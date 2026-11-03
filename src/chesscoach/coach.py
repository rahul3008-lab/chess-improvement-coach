"""Grounded local explanations; model adapters can consume the same evidence bundle."""

from typing import Protocol

import chess
from sqlalchemy import select

from chesscoach.reports import overview
from chesscoach.storage.models import Recommendation, Weakness


class CoachAdapter(Protocol):
    def explain(self, question: str, evidence: dict) -> str: ...


def evidence_bundle(db, player_id):
    with db.session() as session:
        weaknesses = session.scalars(
            select(Weakness).where(Weakness.player_id == player_id, Weakness.scope == "all")
        ).all()
        recommendations = session.scalars(
            select(Recommendation).where(Recommendation.player_id == player_id)
        ).all()
        return {
            "profile": overview(db, player_id),
            "weaknesses": [
                {"topic": w.topic, "confidence": w.confidence, "evidence": w.evidence} for w in weaknesses
            ],
            "recommendations": [
                {"topic": r.topic, "rationale": r.rationale, "minutes": r.daily_minutes}
                for r in recommendations
            ],
        }


def explain_position(evaluation):
    board = chess.Board(evaluation.fen)
    before = evaluation.cp_before * (1 if board.turn else -1)
    after = evaluation.cp_after * (1 if board.turn else -1)
    best = board.san(chess.Move.from_uci(evaluation.best_uci)) if evaluation.best_uci else "No move"
    played = board.san(chess.Move.from_uci(evaluation.played_uci))
    return (
        f"At ply {evaluation.ply}, the engine preferred {best} to {played}. "
        f"The evaluation from the mover's perspective changed from {before / 100:+.2f} to {after / 100:+.2f} "
        f"(loss {evaluation.loss}cp; search depth {evaluation.depth}). "
        "Mate scores use a display sentinel, so centipawn loss near mate is not a calibrated measure. "
        "The principal variation is evidence for the engine's choice; it does not establish the conceptual cause."
    )
