from __future__ import annotations

import shutil
from typing import Protocol

import chess
import chess.engine

from chesscoach.pgn import digest


class AnalysisEngine(Protocol):
    identity: dict

    def evaluate(self, board: chess.Board) -> dict: ...
    def close(self) -> None: ...


class Stockfish:
    def __init__(self, settings, depth=None, profile="fast"):
        path = shutil.which(settings.engine.path)
        if path is None:
            raise ValueError(
                "Stockfish executable not found. Install Stockfish and set STOCKFISH_PATH or engine.path."
            )
        self.engine = chess.engine.SimpleEngine.popen_uci(path, timeout=60)
        self.engine.configure({"Threads": settings.engine.threads, "Hash": settings.engine.hash_mb})
        self.depth = depth or (
            settings.analysis.deep_depth if profile == "deep" else settings.analysis.fast_depth
        )
        self.seconds = settings.analysis.deep_seconds if profile == "deep" else settings.analysis.seconds
        self.multipv = settings.engine.multipv
        self.identity = {
            "engine": self.engine.id,
            "depth": self.depth,
            "seconds": self.seconds,
            "threads": settings.engine.threads,
            "hash_mb": settings.engine.hash_mb,
            "multipv": self.multipv,
            "analysis_version": 2,
        }

    def evaluate(self, board):
        if not board.is_valid():
            raise ValueError("Refusing to send an invalid position to the engine.")
        if board.is_game_over(claim_draw=False):
            outcome = board.outcome()
            cp = (100000 if outcome.winner else -100000) if outcome.winner is not None else 0
            return {"cp": cp, "mate": 0 if board.is_checkmate() else None, "pv": [], "lines": [], "depth": 0}
        lines = self.engine.analyse(
            board, chess.engine.Limit(depth=self.depth, time=self.seconds), multipv=self.multipv
        )
        results = []
        for line in lines:
            score = line["score"].white()
            results.append(
                {
                    "cp": score.score(mate_score=100000),
                    "mate": score.mate(),
                    "pv": [move.uci() for move in line.get("pv", [])],
                    "depth": line.get("depth", 0),
                }
            )
        return {**results[0], "lines": results}

    def play(self, board, skill=10, seconds=0.3):
        if not board.is_valid():
            raise ValueError("Invalid position.")
        if board.is_game_over():
            return None
        options = {"Skill Level": max(0, min(20, skill))} if "Skill Level" in self.engine.options else {}
        return self.engine.play(board, chess.engine.Limit(time=seconds), options=options).move

    def close(self):
        self.engine.quit()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def cache_key(board, config_hash):
    # FEN alone misses repetition history. Keep the complete reversible/history context.
    return digest(
        {"root": board.root().fen(), "moves": [m.uci() for m in board.move_stack], "config": config_hash}
    )
