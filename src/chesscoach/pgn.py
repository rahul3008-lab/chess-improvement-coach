from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone

import chess.pgn


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_games(text: str):
    stream = io.StringIO(text)
    while (game := chess.pgn.read_game(stream)) is not None:
        if game.errors:
            raise ValueError(f"Invalid PGN: {game.errors[0]}")
        board = game.board()
        if not board.is_valid():
            raise ValueError("PGN has an invalid initial position.")
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                raise ValueError("PGN contains an illegal move.")
            board.push(move)
        yield game


def content_hash(game):
    return digest(
        {
            "fen": game.board().fen(),
            "moves": [m.uci() for m in game.mainline_moves()],
            "variant": type(game.board()).__name__,
        }
    )


def played_at(game, metadata):
    if metadata.get("end_time"):
        return datetime.fromtimestamp(metadata["end_time"], timezone.utc).replace(tzinfo=None)
    for date_key in ("UTCDate", "Date"):
        try:
            return datetime.strptime(
                game.headers.get(date_key, "") + " " + game.headers.get("UTCTime", "00:00:00"),
                "%Y.%m.%d %H:%M:%S",
            )
        except ValueError:
            pass
    return None
