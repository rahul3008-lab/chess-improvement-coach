import httpx
from conftest import FAKE_CONFIG, FakeEngine
from sqlalchemy import func, select

from chesscoach.config import ProviderSettings
from chesscoach.jobs import enqueue, run_workers
from chesscoach.practice import generate
from chesscoach.providers.chesscom import ChessCom
from chesscoach.reports import overview, refresh_coaching
from chesscoach.storage.models import Account, AnalysisJob, Game
from chesscoach.sync import sync_account


def test_multi_player_incremental_analysis_and_progress(db, raw_games, settings):
    state = {
        "alpha": raw_games[:1],
        "beta": [
            {**raw_games[1], "uuid": "beta-game", "pgn": raw_games[1]["pgn"].replace('"alpha"', '"beta"')}
        ],
    }

    def handler(request):
        parts = request.url.path.split("/")
        username = parts[3]
        if request.url.path.endswith("/archives"):
            return httpx.Response(
                200, json={"archives": [f"https://api.chess.com/pub/player/{username}/games/2026/10"]}
            )
        if "/games/" in request.url.path:
            return httpx.Response(200, json={"games": state[username]})
        return httpx.Response(200, json={"username": username})

    provider = ChessCom(
        ProviderSettings(request_interval=0), httpx.Client(transport=httpx.MockTransport(handler))
    )
    a, first = sync_account(db, provider, "alpha", settings.providers)
    b, _ = sync_account(db, provider, "beta", settings.providers)
    with db.session() as session:
        player_a, player_b = session.get(Account, a).player_id, session.get(Account, b).player_id
    enqueue(db, player_a, FAKE_CONFIG)
    enqueue(db, player_b, FAKE_CONFIG)
    assert run_workers(db, settings, factory=lambda _: FakeEngine()).completed == 2
    refresh_coaching(db, player_b)
    before_b = overview(db, player_b)
    state["alpha"] = raw_games
    _, second = sync_account(db, provider, "alpha", settings.providers)
    assert first.new == 1 and second.new == 1 and second.skipped == 1
    assert enqueue(db, player_a, FAKE_CONFIG)[0] == 1
    assert run_workers(db, settings, player_a, factory=lambda _: FakeEngine()).completed == 1
    refresh_coaching(db, player_a)
    assert generate(db, player_a) > 0
    assert overview(db, player_a)["analysed_games"] == 2
    assert overview(db, player_b) == before_b
    _, third = sync_account(db, provider, "alpha", settings.providers)
    assert third.new == 0 and enqueue(db, player_a, FAKE_CONFIG)[0] == 0
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(Game)) == 3
        assert session.scalar(select(func.count()).select_from(AnalysisJob)) == 3
