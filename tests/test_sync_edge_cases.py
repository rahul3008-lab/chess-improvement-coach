import httpx
from conftest import FAKE_CONFIG, FakeEngine
from sqlalchemy import select
from test_sync import adapter, response

from chesscoach.jobs import enqueue, run_workers
from chesscoach.storage.models import Account, AnalysisJob, Game, SyncRun
from chesscoach.sync import sync_account


def test_unavailable_archive_is_partial_and_retried(db, raw_games, settings):
    urls = [f"https://api.chess.com/pub/player/alpha/games/2026/{m}" for m in ["09", "10"]]
    failed = True

    def handler(request):
        if str(request.url) == urls[0] and failed:
            return httpx.Response(404)
        return response(request, raw_games[:1], urls)

    provider = adapter(handler)
    account_id, result = sync_account(db, provider, "alpha", settings.providers)
    assert result.new == 1 and result.unavailable_archives == [urls[0]]
    with db.session() as session:
        assert session.get(Account, account_id).last_sync is None
        assert session.scalars(select(SyncRun)).first().status == "partial"
    failed = False
    _, result = sync_account(db, provider, "alpha", settings.providers)
    assert result.unavailable_archives == [] and result.new == 0


def test_unchanged_remote_game_does_not_reparse_pgn(db, raw_games, settings, monkeypatch):
    url = "https://api.chess.com/pub/player/alpha/games/2026/10"
    provider = adapter(lambda request: response(request, raw_games[:1], [url]))
    sync_account(db, provider, "alpha", settings.providers)

    def forbidden(_):
        raise AssertionError("Unchanged PGN was reparsed")

    monkeypatch.setattr("chesscoach.sync.parse_games", forbidden)
    _, result = sync_account(db, provider, "alpha", settings.providers)
    assert result.skipped == 1


def test_metadata_change_does_not_requeue_engine(db, raw_games, settings):
    url = "https://api.chess.com/pub/player/alpha/games/2026/10"
    state = [raw_games[0]]
    provider = adapter(lambda request: response(request, state, [url]))
    account_id, _ = sync_account(db, provider, "alpha", settings.providers)
    with db.session() as session:
        owner = session.get(Account, account_id).player_id
    enqueue(db, owner, FAKE_CONFIG)
    run_workers(db, settings, owner, factory=lambda _: FakeEngine())
    state[0] = {**state[0], "rated": True}
    sync_account(db, provider, "alpha", settings.providers)
    assert enqueue(db, owner, FAKE_CONFIG)[0] == 0
    with db.session() as session:
        assert session.scalars(select(AnalysisJob)).first().status == "completed"
        assert session.scalars(select(Game)).first().details["rated"] is True
