from functools import lru_cache
from pathlib import Path

import chess
import chess.svg
import streamlit as st
import streamlit.components.v1 as components

_board = components.declare_component("chesscoach_board_v2", path=str(Path(__file__).parent / "board"))


@lru_cache(maxsize=1)
def piece_sprites():
    return {
        symbol: {"svg": chess.svg.piece(piece), "name": piece.unicode_symbol(), "symbol": symbol}
        for symbol in "PNBRQKpnbrqk"
        for piece in [chess.Piece.from_symbol(symbol)]
    }


def board_widget(board, key, orientation="white", disabled=False, feedback=None, thinking=False):
    sprites = piece_sprites()
    pieces = {
        chess.square_name(square): sprites[piece.symbol()] for square, piece in board.piece_map().items()
    }
    value = _board(
        fen=board.fen(),
        pieces=pieces,
        sprites=sprites,
        legal=[move.uci() for move in board.legal_moves],
        orientation=orientation,
        turn="White" if board.turn else "Black",
        disabled=disabled,
        ack=st.session_state.get(f"{key}_event"),
        feedback=feedback,
        thinking=thinking,
        key=key,
        default=None,
    )
    if (
        value
        and value.get("fen") == board.fen()
        and value.get("event_id") != st.session_state.get(f"{key}_event")
    ):
        st.session_state[f"{key}_event"] = value["event_id"]
        try:
            move = chess.Move.from_uci(value["move"])
            return move if move in board.legal_moves and not disabled else None
        except (ValueError, KeyError):
            return None
    return None
