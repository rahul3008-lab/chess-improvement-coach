from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from chesscoach.config import ProviderSettings
from chesscoach.providers.chesscom import ChessCom
from chesscoach.storage.models import Account, Archive, Game, SyncRun, now
from chesscoach.sync import sync_account


def adapter(handler):
    return ChessCom(
        ProviderSettings(request_interval=0, retries=0), httpx.Client(transport=httpx.MockTransport(handler))
    )


def response(request, games, urls):
    if request.url.path.endswith("/archives"):
        return httpx.Response(200, json={"archives": urls})
    if "/games/" in request.url.path:
        return httpx.Response(200, json={"games": games}, headers={"ETag": '"fixture"'})
    return httpx.Response(200, json={"username": "alpha", "player_id": 42})


def test_conditional_incremental_and_metadata_only_change(db, raw_games, settings):
    url = "https://api.chess.com/pub/player/alpha/games/2026/10"
    state = {"games": raw_games[:1], "not_modified": False}
    requests = []

    def handler(request):
        requests.append(request)
        if "/games/2026" in request.url.path and state["not_modified"]:
            assert request.headers["If-None-Match"] == '"fixture"'
            return httpx.Response(304)
        return response(request, state["games"], [url])

    provider = adapter(handler)
    account_id, first = sync_account(db, provider, "alpha", settings.providers)
    assert first.new == 1
    state["not_modified"] = True
    _, second = sync_account(db, provider, "alpha", settings.providers)
    assert second.new == 0 and second.archives_cached == 1
    state["not_modified"] = False
    state["games"] = raw_games
    _, third = sync_account(db, provider, "alpha", settings.providers)
    assert third.new == 1 and third.skipped == 1
    with db.session() as session:
        game = session.scalars(select(Game)).first()
        old_content = game.content_hash
    state["games"][0] = {**raw_games[0], "rated": True}
    _, fourth = sync_account(db, provider, "alpha", settings.providers)
    assert fourth.updated == 1 and fourth.new == 0
    with db.session() as session:
        assert session.scalars(select(Game)).first().content_hash == old_content
        assert session.get(Account, account_id).last_sync is not None
        assert session.scalar(select(func.count()).select_from(Game)) == 2


def test_historical_caching_and_revalidation(db, raw_games, settings):
    urls = [f"https://api.chess.com/pub/player/alpha/games/2020/{month:02}" for month in [1, 2, 3]]
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return response(request, raw_games[:1], urls)

    provider = adapter(handler)
    sync_account(db, provider, "alpha", settings.providers)
    requested.clear()
    _, result = sync_account(db, provider, "alpha", settings.providers)
    assert result.archives_cached == 1
    assert urls[0] not in requested and urls[1] in requested and urls[2] in requested
    with db.session() as session:
        session.scalar(select(Archive).where(Archive.url == urls[0])).checked_at = now() - timedelta(days=60)
    requested.clear()
    sync_account(db, provider, "alpha", settings.providers)
    assert urls[0] in requested


def test_partial_sync_recovers_without_duplicates(db, raw_games, settings):
    urls = [f"https://api.chess.com/pub/player/alpha/games/2026/{m}" for m in ["09", "10"]]
    failing = True

    def handler(request):
        if str(request.url) == urls[1] and failing:
            return httpx.Response(503)
        return response(request, raw_games[:1] if str(request.url) == urls[0] else raw_games, urls)

    provider = adapter(handler)
    with pytest.raises(httpx.HTTPStatusError):
        sync_account(db, provider, "alpha", settings.providers)
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(Game)) == 1
        assert session.scalars(select(SyncRun)).first().status == "failed"
        assert session.scalars(select(Account)).first().last_sync is None
    failing = False
    _, result = sync_account(db, provider, "alpha", settings.providers)
    assert result.new == 1
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(Game)) == 2


def test_rejects_remote_archive_redirects_and_other_accounts():
    provider = adapter(lambda _: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="outside"):
        provider._request("https://evil.example/pub/player")
    with pytest.raises(ValueError, match="does not belong"):
        provider.fetch_games("alpha", "https://api.chess.com/pub/player/beta/games/2026/10")


def test_retry_timeout_and_rate_limit(monkeypatch):
    monkeypatch.setattr("chesscoach.providers.chesscom.time.sleep", lambda _: None)
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("timeout", request=request)
        if attempts == 2:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"username": "alpha"})

    provider = ChessCom(
        ProviderSettings(retries=2, request_interval=0), httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert provider.get_player("alpha")["username"] == "alpha"
    assert attempts == 3


def test_missing_player_not_registered(db, settings):
    provider = adapter(lambda _: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        sync_account(db, provider, "missing", settings.providers)
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(Account)) == 0
