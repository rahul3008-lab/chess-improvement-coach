# ♞ Chess Improvement Coach

**Your games. Your patterns. Your next good move.**

A local-first Python application for collecting games, analysing them with Stockfish,
and practising critical positions. Multiple players share an installation with separate
training histories; one player can explicitly link several external chess accounts.

[![CI](https://github.com/rahul3008-lab/chess-improvement-coach/actions/workflows/ci.yml/badge.svg)](https://github.com/rahul3008-lab/chess-improvement-coach/actions/workflows/ci.yml)

Independent project; not affiliated with or endorsed by Chess.com. Uses the official
[Published Data API](https://support.chess.com/en/articles/9650547-published-data-api).
No Chess.com password, cloud subscription, or language-model API key is required.

## Install

Requires **Python 3.11+** and an independently installed Stockfish executable.

```bash
git clone https://github.com/rahul3008-lab/chess-improvement-coach.git
cd chess-improvement-coach
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
python -m pip install .
```

Install Stockfish using your OS package manager or the
[official distribution](https://stockfishchess.org/download/).
macOS: `brew install stockfish`; Debian/Ubuntu: `sudo apt install stockfish`.
If the executable is outside PATH, configure its location:

```bash
export STOCKFISH_PATH=/path/to/stockfish
# Windows PowerShell: $env:STOCKFISH_PATH="C:\path\stockfish.exe"
chesscoach doctor
```

Isolated installations also work: `pipx install .` or `uv tool install .`.

## Start with your games

```bash
chesscoach sync --provider chess.com --username YOUR_USERNAME
chesscoach analyze --provider chess.com --username YOUR_USERNAME --limit 5
chesscoach practice generate --provider chess.com --username YOUR_USERNAME
chesscoach dashboard
```

Open [the local dashboard](http://127.0.0.1:8501). It binds to loopback only.
Start with a small analysis sample, then expand. `--limit` caps new jobs queued and
jobs processed; it does not discard games. A separate worker can run alongside the UI.

```bash
chesscoach update --provider chess.com --username YOUR_USERNAME
# Sync, queue missing/current-configuration analysis, start a background worker.
chesscoach update --provider chess.com --username YOUR_USERNAME --limit 5 --foreground
chesscoach analyze --provider chess.com --username YOUR_USERNAME --profile deep --limit 1
chesscoach analyze --provider chess.com --username YOUR_USERNAME --depth 18 --limit 1
chesscoach progress --provider chess.com --username YOUR_USERNAME --days 30
```

Depth and time are **upper limits**, whichever is reached first. Fast defaults to
depth 12 / 0.15 seconds per position; deep uses depth 18 / 2 seconds. Set
`analysis.seconds: null` (or `deep_seconds: null`) for depth-only searches. Achieved
depth is recorded. Engine/configuration changes preserve earlier results as versions.

## Players and linked accounts

```bash
chesscoach users list
chesscoach users add "Tournament player"
chesscoach users link 2 --provider chess.com --username SECOND_ACCOUNT
chesscoach sync --provider chess.com --username SECOND_ACCOUNT
# A different username otherwise creates its own independent player.
chesscoach sync --provider chess.com --username ANOTHER_PLAYER
chesscoach import-pgn --file my-games.pgn --user "PGN Header Name" --player-id 2
chesscoach analyze --provider local --username "PGN Header Name" --limit 5
```

`chess.com`/`chess-com` resolve to `chesscom`; `local-pgn`/`pgn` resolve to `local`.
Equal usernames on different providers never merge players. An account already owned
by another player cannot be silently transferred. PGN imports preserve source
basenames/checksums. Use the exact White/Black name for `--user`.

## Queue and recovery

```bash
chesscoach analyze --provider chess.com --username YOUR_USERNAME --queue-only
chesscoach worker --workers 2 --max-jobs 20
chesscoach jobs list --player-id 1
chesscoach jobs cancel 123
chesscoach jobs retry 123
chesscoach worker
```

Statuses: pending, running, completed, failed, cancelled. Atomic claims and expiring,
renewed leases prevent stale worker commits and recover interrupted analysis. Ctrl-C
requests graceful shutdown. After a forced kill, run a worker once the lease expires
(default 15 minutes). Workers drain the queue then exit; no system daemon is installed.
Background output lives in the data directory's `logs/` folder.

## Practice and dashboard

Fourteen views cover overview, ratings, game replay, openings, tactical/phase diagnostics,
practice, learning, scheduled reviews, reports, goals, imports, and settings. Analytics
filter by account, time control, colour, period, opening/ECO and online rating. Practice,
recommendations and period reports cover the whole player, as labelled in the UI.

```bash
chesscoach practice generate --provider chess.com --username YOUR_USERNAME --mode mistakes
chesscoach practice start --provider chess.com --username YOUR_USERNAME --mode mistakes
```

Modes: `mistakes`, `tactics`, `endgame`, `critical`, `opening`, `calculation`. The
dashboard also has engine play with skill 0–20 and custom FENs. Its clickable board
has no external JS/CDN dependency. python-chess validates moves, castling, en passant
and selected promotion pieces server-side. Terminal sessions accept SAN and UCI.

Exercises use recorded legal engine variations. Second MultiPV choices within 50cp
are excluded. Ordinary modes grade the first preferred move; calculation asks for up
to five plies. Other good moves are not automatically accepted as equivalent: these
are recorded-line exercises, not proofs of uniqueness. Tactics are check/capture
candidates, not an exhaustive motif taxonomy. Hints/reveals prevent unassisted streak
advancement. Attempts retain moves, duration, hints and sessions; correct unassisted
reviews double intervals up to 90 days. Superseded results leave the due queue.

Endgames optionally probe **local Syzygy files** at a user-supplied directory.
Table files are not bundled/downloaded automatically; missing coverage is reported.

Reports cite actual game/ply/evaluation evidence and distinguish measured engine loss
from uncertain conceptual causes. Adjacent-period comparisons require at least 20
analysed games per period. Online provider/account/time-control ratings remain separate
from explicitly entered FIDE information. Road to GM is a long-term goals notebook,
not a title prediction. Original study outlines and legitimate external links retain
licence metadata; third-party courses are not copied.

## Configuration and portability

```bash
chesscoach config init
chesscoach doctor
chesscoach --help
```

YAML configuration and data use platform-appropriate user directories, independent
of the checkout. `doctor` reports paths. See [config/example.yaml](config/example.yaml).

| Variable | Purpose |
| --- | --- |
| `CHESSCOACH_CONFIG` | Explicit YAML configuration |
| `CHESSCOACH_DATA_DIR` | Data/log directory override |
| `CHESSCOACH_DATABASE_URL` | SQLAlchemy database URL |
| `STOCKFISH_PATH` | Engine executable |

To move computers, install the package and Stockfish, then transfer a **consistent
SQLite backup** and config. Stop dashboard/workers before file copies, or use SQLite's
backup API. Copying a live DB without its WAL sidecars can lose data. Players, analyses,
queue state and practice history persist in SQLite; choose a compatible engine path
on the new computer. Alembic migrations ship in the wheel and run on startup.
PostgreSQL URLs and a `postgres` dependency extra are extension points; this release
is verified with SQLite, not a PostgreSQL deployment.

## Privacy and scope

This is a **trusted local multi-profile application**, not an authenticated service.
Anyone with machine/database access can access its profiles. Public exposure requires
authentication, authorisation and server-side ownership enforcement. Downloaded data,
PGNs, logs, credentials, reports and virtual environments are excluded from Git.

There are **no cloud AI calls** in this release. Deterministic grounded explanations
work locally; `CoachAdapter` is the model extension point. An OpenAI adapter, credentials
flow and conversational UI remain future work. Full Lichess support, a repertoire
editor, exhaustive tactical/positional diagnosis and a master-game database also remain
future work. Standard chess is supported; unsupported variants are counted/skipped.
Malformed standard PGN rolls back its archive and can be retried after correction.

Chess.com may return stale data or unavailable archives. Recent months are always
rechecked with HTTP validators and older archives periodically revalidated. Missing
404/410 archives yield a persisted **partial** sync and remain eligible for retry,
without changing the last fully successful sync timestamp.

## Development

```bash
python -m pip install -e '.[dev]'
pytest -q
ruff check src tests
ruff format --check src tests
python -m build
```

Tests cover player separation, providers, deduplication, incremental/interrupted sync,
queue recovery/concurrency, configuration changes, history-aware caching, legality,
grading, CLI and dashboard views. Offline fixtures are deterministic. Real engine
tests run with `stockfish` on PATH, otherwise explicitly skip. CI tests Python 3.11–3.13
with Stockfish, plus Windows/macOS package portability.

See [architecture](docs/architecture.md), [providers](docs/providers.md),
[validation](docs/validation.md), and [contributing](CONTRIBUTING.md).
GPL-3.0-or-later; see [LICENSE](LICENSE). python-chess is GPL-3.0-or-later;
Stockfish is independently installed and not bundled.
