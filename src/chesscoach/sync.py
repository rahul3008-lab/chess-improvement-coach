from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select

from chesscoach.identity import resolve_account
from chesscoach.pgn import content_hash, digest, parse_games, played_at
from chesscoach.storage.models import Account, AccountGame, Archive, Game, RatingPoint, SyncRun, now


@dataclass
class SyncResult:
    previous: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0
    archives_fetched: int = 0
    archives_cached: int = 0
    unsupported: int = 0
    unavailable_archives: list[str] = field(default_factory=list)


def store_game(session, account, raw, provenance):
    checksum = digest(raw)
    remote_id = raw.get("uuid") or raw.get("url")
    if remote_id and account.provider != "local":
        existing = session.scalar(
            select(Game).where(Game.provider == account.provider, Game.source_id == str(remote_id))
        )
        if existing and existing.checksum == checksum:
            link = session.get(AccountGame, (account.id, existing.id))
            if link:
                link.provenance = {
                    **link.provenance,
                    **provenance,
                    "sources": list(
                        dict.fromkeys(link.provenance.get("sources", []) + provenance.get("sources", []))
                    ),
                }
                return "skipped"  # No PGN parse or engine work for unchanged, already-linked games.
    games = list(parse_games(raw.get("pgn", "")))
    if len(games) != 1:
        raise ValueError("A source game must contain exactly one PGN record.")
    parsed = games[0]
    if type(parsed.board()).__name__ != "Board" or parsed.board().chess960:
        return "unsupported"
    position_hash = content_hash(parsed)
    headers = dict(parsed.headers)
    if account.provider == "local":
        # Deliberately account-scoped: importing a file never merges unrelated players' provenance.
        stable_id = f"{account.id}:{digest({'headers': headers, 'moves': position_hash})}"
    else:
        stable_id = str(
            raw.get("uuid") or raw.get("url") or digest({"headers": headers, "moves": position_hash})
        )
    game = session.scalar(select(Game).where(Game.provider == account.provider, Game.source_id == stable_id))
    status = "skipped"
    if game is None:
        game = Game(
            provider=account.provider,
            source_id=stable_id,
            pgn=raw["pgn"],
            checksum=checksum,
            content_hash=position_hash,
            headers=headers,
            details={},
            time_class="unknown",
        )
        session.add(game)
        session.flush()
        status = "new"
    elif game.checksum != checksum:
        status = "updated"
    if status != "skipped":
        game.pgn, game.checksum, game.content_hash = raw["pgn"], checksum, position_hash
        game.headers, game.details, game.url = (
            headers,
            {k: v for k, v in raw.items() if k != "pgn"},
            raw.get("url"),
        )
        game.time_class, game.played_at = raw.get("time_class", "unknown"), played_at(parsed, raw)
    white = headers.get("White", "").lower() == account.username
    black = headers.get("Black", "").lower() == account.username
    if not white and not black:
        raise ValueError(
            f"PGN does not include account {account.username}; use the player's PGN name as --user."
        )
    color = "white" if white else "black"
    value = raw.get(color, {}).get("rating") or headers.get("WhiteElo" if white else "BlackElo")
    rating = int(value) if str(value).isdigit() else None
    link = session.get(AccountGame, (account.id, game.id))
    if not link:
        link = AccountGame(
            account_id=account.id, game_id=game.id, color=color, rating=rating, provenance=provenance
        )
        session.add(link)
    else:
        link.rating, link.color = rating, color
        sources = list(dict.fromkeys(link.provenance.get("sources", []) + provenance.get("sources", [])))
        link.provenance = {**link.provenance, **provenance, "sources": sources}
    if rating is not None:
        point = session.scalar(
            select(RatingPoint).where(RatingPoint.account_id == account.id, RatingPoint.game_id == game.id)
        )
        if point is None:
            point = RatingPoint(
                account_id=account.id, game_id=game.id, time_class=game.time_class, rating=rating
            )
            session.add(point)
        point.rating, point.played_at, point.time_class = rating, game.played_at, game.time_class
    return status


def sync_account(db, provider, username, settings, player_id=None, revalidate=False):
    profile = provider.get_player(username)  # Do not register a nonexistent remote account.
    with db.session() as session:
        account = resolve_account(session, provider.id, username, player_id)
        account.details, account.external_id = profile, str(profile.get("player_id", "")) or None
        account_id = account.id
        result = SyncResult(
            previous=session.scalar(
                select(func.count()).select_from(AccountGame).where(AccountGame.account_id == account_id)
            )
        )
        run = SyncRun(account_id=account_id)
        session.add(run)
        session.flush()
        run_id = run.id
    try:
        urls = sorted(provider.list_game_archives(username))
        for url in urls:
            with db.session() as session:
                cached = session.scalar(
                    select(Archive).where(Archive.account_id == account_id, Archive.url == url)
                )
                recent = url in urls[-2:] or url.endswith(now().strftime("/%Y/%m"))
                fresh = cached and cached.checked_at > now() - timedelta(
                    days=settings.historical_revalidate_days
                )
                if cached and fresh and not recent and not revalidate:
                    result.archives_cached += 1
                    continue
                etag, modified = (cached.etag, cached.last_modified) if cached else (None, None)
            try:
                remote = provider.fetch_games(username, url, etag, modified)
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in {404, 410}:
                    raise
                # Discovery can list removed/unavailable archives. Never cache these as complete.
                result.unavailable_archives.append(url)
                continue
            # Entire archive and its checkpoint commit together. Interrupted downloads can safely resume.
            with db.session() as session:
                account = session.get(Account, account_id)
                cached = session.scalar(
                    select(Archive).where(Archive.account_id == account_id, Archive.url == url)
                )
                if cached is None:
                    cached = Archive(account_id=account_id, url=url)
                    session.add(cached)
                if not remote.not_modified:
                    for raw in remote.games:
                        if raw.get("rules", "chess") != "chess":
                            result.unsupported += 1
                            continue
                        status = store_game(session, account, raw, {"archive": url, "sources": [url]})
                        setattr(result, status, getattr(result, status) + 1)
                    cached.checksum = digest(remote.games)
                else:
                    result.archives_cached += 1
                cached.etag, cached.last_modified, cached.checked_at = (
                    remote.etag,
                    remote.last_modified,
                    now(),
                )
                result.archives_fetched += 1
                session.get(SyncRun, run_id).counts = asdict(result)
        with db.session() as session:
            account = session.get(Account, account_id)
            if not result.unavailable_archives:
                account.last_sync = now()
            run = session.get(SyncRun, run_id)
            run.status, run.finished_at, run.counts = (
                "partial" if result.unavailable_archives else "completed",
                now(),
                asdict(result),
            )
    except BaseException as error:
        with db.session() as session:
            run = session.get(SyncRun, run_id)
            run.status, run.finished_at, run.error = "failed", now(), str(error)[:2000]
        raise
    return account_id, result


def import_pgn(db, path: Path, username: str, player_id=None):
    text = path.read_text(encoding="utf-8-sig")
    parsed_games = list(parse_games(text))  # Validate the complete file before writing anything.
    if not parsed_games:
        raise ValueError("The PGN file contains no games.")
    result = SyncResult()
    with db.session() as session:
        account = resolve_account(session, "local", username, player_id)
        result.previous = session.scalar(
            select(func.count()).select_from(AccountGame).where(AccountGame.account_id == account.id)
        )
        for game in parsed_games:
            status = store_game(
                session, account, {"pgn": str(game)}, {"sources": [path.name], "file_checksum": digest(text)}
            )
            setattr(result, status, getattr(result, status) + 1)
        account.last_sync = now()
        session.add(
            SyncRun(account_id=account.id, status="completed", finished_at=now(), counts=asdict(result))
        )
        return account.id, result
