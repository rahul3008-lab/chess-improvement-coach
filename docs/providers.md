# Provider extensions

Implement `ChessProvider`, expose capability flags, and register a trusted adapter.
Each provider owns its API pagination, timeout/retry behaviour and archive semantics.
`ArchiveResult.games` contains dictionaries with a single `pgn`, stable `uuid` or `url`,
and optional time-class, timestamp and account-rating metadata.

```python
from chesscoach.providers import ProviderRegistry
from chesscoach.providers.base import ArchiveResult, Capabilities

class ExampleProvider:
    id = "example"
    capabilities = Capabilities(archives=True)

    def __init__(self, settings=None):
        self.settings = settings

    def get_player(self, username):
        return {"username": username}

    def list_game_archives(self, username):
        return ["example:archive"]

    def fetch_games(self, username, archive, etag=None, last_modified=None):
        return ArchiveResult(games=[])  # Replace with documented API access.

    def get_rating_history(self, username):
        return {}  # Unsupported; capability flag is False.

    def close(self):
        pass

registry = ProviderRegistry()
registry.register("example", ExampleProvider)
```

Or publish a Python plugin with an entry point:

```toml
[project.entry-points."chesscoach.providers"]
example = "example_chess_provider:ExampleProvider"
```

Only install trusted plugins; they execute Python with your local rights. Archive IDs
need not be URLs; providers without monthly archives can expose logical checkpoints,
with a custom sync strategy when semantics differ.

Chess.com validates official API origin and account paths, requests serially, and uses
conditional HTTP headers. Its stats endpoint provides current/best snapshots; rating
history is derived from individual imported games rather than invented historic data.

Engine plugins implement `AnalysisEngine.identity`, `evaluate(board)` and `close()`.
Evaluation returns White-POV centipawns, mate distance, achieved depth, a legal UCI PV,
and optional MultiPV lines. Include every result-affecting setting/version in identity.
Model plugins implement `CoachAdapter.explain(question, evidence)` using the player-scoped
evidence bundle and must preserve source citations and uncertainty.
