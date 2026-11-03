from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Capabilities:
    archives: bool = False
    ratings: bool = False
    clocks: bool = False
    conditional_requests: bool = False


@dataclass
class ArchiveResult:
    games: list[dict] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


class ChessProvider(Protocol):
    id: str
    capabilities: Capabilities

    def get_player(self, username: str) -> dict: ...
    def list_game_archives(self, username: str) -> list[str]: ...
    def fetch_games(self, username: str, archive: str, etag=None, last_modified=None) -> ArchiveResult: ...
    def get_rating_history(self, username: str) -> dict: ...
    def close(self) -> None: ...
