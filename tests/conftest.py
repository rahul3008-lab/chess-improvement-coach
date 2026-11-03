from pathlib import Path

import pytest

from chesscoach.config import Settings
from chesscoach.pgn import parse_games
from chesscoach.storage.db import Database
from chesscoach.sync import import_pgn

FIXTURE = Path(__file__).parent / "fixtures" / "games.pgn"
FAKE_CONFIG = {
    "engine": {"name": "FakeEngine-for-tests"},
    "depth": 5,
    "seconds": None,
    "threads": 1,
    "hash_mb": 8,
    "multipv": 2,
    "analysis_version": 1,
}


class FakeEngine:
    identity = FAKE_CONFIG
    calls = 0

    def evaluate(self, board):
        type(self).calls += 1
        cp = -300 if len(board.move_stack) % 2 else 0
        moves = []
        variation = board.copy()
        for _ in range(7):
            candidates = list(variation.legal_moves)
            if not candidates:
                break
            move = candidates[0]
            moves.append(move.uci())
            variation.push(move)
        return {
            "cp": cp,
            "mate": None,
            "pv": moves,
            "depth": 5,
            "lines": [{"cp": cp, "pv": moves}, {"cp": cp - 100, "pv": []}],
        }

    def close(self):
        pass


@pytest.fixture
def db(tmp_path):
    result = Database(f"sqlite:///{tmp_path / 'test.db'}")
    result.migrate()
    yield result
    result.engine.dispose()


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def imported(db):
    account_id, _ = import_pgn(db, FIXTURE, "alpha")
    from chesscoach.storage.models import Account

    with db.session() as session:
        return session.get(Account, account_id).player_id


@pytest.fixture
def raw_games():
    return [
        {
            "pgn": str(game),
            "uuid": f"fixture-{index}",
            "url": f"https://www.chess.com/game/live/{index}",
            "time_class": "rapid",
            "rules": "chess",
            "end_time": 1791600000 + index,
            "white": {"rating": 1200},
            "black": {"rating": 1250},
        }
        for index, game in enumerate(parse_games(FIXTURE.read_text()))
    ]
