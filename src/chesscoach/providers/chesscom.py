from __future__ import annotations

import re
import time
from urllib.parse import quote, urlparse

import httpx

from chesscoach.config import ProviderSettings
from chesscoach.providers.base import ArchiveResult, Capabilities


class ChessCom:
    """Serial, conditional access to the official read-only Published Data API."""

    id = "chesscom"
    capabilities = Capabilities(archives=True, ratings=True, clocks=True, conditional_requests=True)
    base_url = "https://api.chess.com/pub"

    def __init__(self, settings: ProviderSettings | None = None, client: httpx.Client | None = None):
        self.settings = settings or ProviderSettings()
        self.client = client or httpx.Client(
            timeout=self.settings.timeout,
            headers={
                "User-Agent": "ChessImprovementCoach/0.1 (https://github.com/rahul3008-lab/chess-improvement-coach)",
                "Accept": "application/json",
            },
        )
        self._last_request = 0.0

    def _request(self, url, headers=None):
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "api.chess.com"
            or not parsed.path.startswith("/pub/")
        ):
            raise ValueError("Rejected archive outside the official Chess.com API.")
        for attempt in range(self.settings.retries + 1):
            time.sleep(max(0, self.settings.request_interval - (time.monotonic() - self._last_request)))
            try:
                self._last_request = time.monotonic()
                response = self.client.get(url, headers=headers or {})
                if response.status_code == 304:
                    return response
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as error:
                retryable = not isinstance(error, httpx.HTTPStatusError) or error.response.status_code in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }
                if not retryable or attempt >= self.settings.retries:
                    raise
                retry_after = (
                    error.response.headers.get("Retry-After", "")
                    if isinstance(error, httpx.HTTPStatusError)
                    else ""
                )
                delay = min(float(retry_after), 60) if retry_after.isdigit() else min(2**attempt, 30)
                time.sleep(delay)
        raise RuntimeError("Unreachable retry state")

    @staticmethod
    def _username(username):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", username):
            raise ValueError("Invalid Chess.com username.")
        return quote(username.lower(), safe="")

    def get_player(self, username):
        return self._request(f"{self.base_url}/player/{self._username(username)}").json()

    def list_game_archives(self, username):
        return self._request(f"{self.base_url}/player/{self._username(username)}/games/archives").json()[
            "archives"
        ]

    def fetch_games(self, username, archive, etag=None, last_modified=None):
        expected = f"{self.base_url}/player/{self._username(username)}/games/"
        if not archive.startswith(expected) or not re.fullmatch(r"\d{4}/\d{2}", archive[len(expected) :]):
            raise ValueError("Archive URL does not belong to this account.")
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        response = self._request(archive, headers)
        return ArchiveResult(
            games=[] if response.status_code == 304 else response.json()["games"],
            etag=response.headers.get("ETag", etag),
            last_modified=response.headers.get("Last-Modified", last_modified),
            not_modified=response.status_code == 304,
        )

    def get_rating_history(self, username):
        # PubAPI supplies current/best stats; historical ratings are derived from individual game records.
        return self._request(f"{self.base_url}/player/{self._username(username)}/stats").json()

    def close(self):
        self.client.close()
