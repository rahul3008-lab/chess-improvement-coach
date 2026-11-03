# Validation record

Local validation on 3 November 2026 used Python 3.12.12, SQLite, and Stockfish 19
on macOS arm64. The committed CI workflow adds Linux/Python 3.11–3.13 and offline
Windows/macOS coverage; those remote results must be checked independently.

Executed checks:

- 47 Python tests passed, including a real Stockfish mate/worker integration, plus
  9 JavaScript board-interaction tests (Node.js 22+).
- Offline end-to-end test: import two independent players, add only new games to one,
  analyse only missing work, preserve the other player's profile and training history.
- Archive revalidation/304 responses, metadata-only edits, missing archives, timeout/
  rate-limit retry, partial imports and interrupted recovery.
- Atomic queue claims, expired leases, cancellation, retry, engine version/configuration
  changes and history-aware position caching.
- Click-to-move, unchanged illegal destinations, immediate legal-move previews,
  server acknowledgements, wrong-answer retries, and special-move previews.
- Practice selection uses SQL to validate current analyses, without rebuilding the
  full move-evaluation history. Practice actions skip the full overview calculations.
- Fixed game-selection boundaries for bulk analysis, plus dashboard refresh as background
  job status changes. Selected batches reuse completed results without expanding into older games.
- SAN/UCI legality including castling, en passant and underpromotion; exact-PV grading,
  calculation length, spaced review, hints, ownership checks and superseded exercises.
- CLI commands, preference-aware curriculum and all fourteen dashboard pages via
  Streamlit AppTest. Live browser smoke checks supplement these server-render tests.
- Ruff lint and format checks; source distribution and wheel build. Wheel includes
  the board component and Alembic baseline, with no downloaded games or databases.
- Installed application used for a live Chess.com import, repeat incremental sync,
  a controlled Stockfish sample, exercise generation and the loopback dashboard.
  Missing remote archives were reported as partial, not fabricated as complete.

Private live-run counts and reports stay outside Git. Fixture scores are deliberately
synthetic and only test mechanics; actual engine tests and live analysis use Stockfish.

Not verified: PostgreSQL, a hosted/authenticated deployment, Syzygy coverage without
user-supplied table files, future provider plugins, cloud model adapters, and long-term
coaching effectiveness. Passing tests do not establish every chess diagnosis or predict
rating improvement/title attainment. See README for the explicit release boundaries.
