# Contributing

Install `.[dev]` using Python 3.11+. Run `pytest -q`, `ruff check src tests` and
`ruff format --check src tests`. Install Stockfish for real engine tests; all other
tests must be offline and deterministic. Add migrations rather than editing a released
schema baseline. Preserve profile separation, original provenance, analysis versioning,
and attempt history. Keep cloud dependencies out of the core workflow.

Retain resource source/licence metadata; avoid interpreting engine loss as proof of
a chess concept. Fixture performance is not real player performance. Never commit
downloaded PGNs, private data, credentials, logs, reports, virtual environments or
engine binaries. Contributions use GPL-3.0-or-later.
