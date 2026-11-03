# Architecture and invariants

CLI and Streamlit use the same identity, sync, queue, reporting, practice and coaching
services. Business logic has no frontend dependency. SQLAlchemy/Alembic provide the
SQLite storage boundary, `ChessProvider` handles source APIs, `AnalysisEngine` handles
UCI, and `CoachAdapter` is the optional language-model extension point.

## Identity

`Player` is a learning identity, not a website username or login. `Account` uniquely
maps canonical provider + normalised username to one player. `AccountGame` holds
perspective, rating and provenance. Provider games are unique within a provider;
opponents can share source analysis without sharing training history. A player's
game total counts a game once; self-play result rows retain both account perspectives.
Local PGNs have account-scoped header/move fingerprints to avoid incorrectly merging
unrelated uploads. Header edits without a provider ID may deliberately create distinct
local imports instead of guessing equivalence.

Models cover profiles/preferences/FIDE metadata, accounts, games, moves/evaluations,
jobs, position cache, archive checkpoints, sync runs, critical-position exercises,
weakness evidence, recommendations, sessions/attempts, snapshots, ratings and goals.
Critical positions link to versioned evaluations rather than duplicating their content
in a standalone table. Original PGN is retained.

## Incremental updates

Recent two listed months and the current month are always checked; older archives
have a configurable revalidation interval and `--revalidate` override. HTTP ETag and
Last-Modified validators avoid unnecessary transfers. Source checksums skip parsing
unchanged linked games; separate initial-position/move checksums invalidate analysis
only when chess content changes. Metadata-only edits preserve analyses.

Each archive and checkpoint commit together. A failed archive rolls back while earlier
committed archives survive. Missing 404/410 archives produce a partial run, stay eligible
for retry and do not update last fully successful sync. Provider-side caching can still
delay discovery. A hard-killed sync may leave a `running` audit record; the next sync
resumes from committed archive checkpoints without trusting that record.

## Queue and analysis

Queue identity: `(game, chess-content hash, analysis-configuration hash)`. Configuration
includes UCI engine identity/version, depth, seconds, threads, hash size, MultiPV and
algorithm version. Reports use one latest completed current-content result per game;
old analyses remain in storage. Pending new settings do not hide an older valid result,
but pending counts remain visible.

Claims use SQL compare-and-swap and lease tokens. Background renewal runs during
engine calls. Cancellation/ownership loss prevents stale completion. Evaluations and
completed status commit atomically. Position cache includes initial board **and full
move history** because repetition context cannot be recovered from FEN alone.

White-POV scores are converted to mover-POV loss. Separate mate fields preserve mate
distance; numeric sentinels are display/sorting aids, not calibrated centipawns.
50/100/200cp move labels are configurable-in-code heuristics, not a proprietary
accuracy score. Only the player's own moves contribute to their diagnosis.

## Practice and coaching

Exercise PVs are legality checked; ambiguous MultiPV choices are excluded when a
second line is available. This is search-limited preference, not proof of uniqueness.
Both session and exercise must belong to the selected player. Practice state keys
include player/mode/exercise. Historical attempts remain after analysis changes.

Weaknesses rank frequency and capped severity with time-control/colour/phase scopes.
Conceptual root causes require review. Recommendations cite evaluation IDs and attach
original study outlines, external links and licence metadata. Online rating only orders
learning materials coarsely; it never establishes title eligibility. Cloud model
adapters must opt in before sending any local evidence externally.

## Operations

SQLite enables foreign keys, WAL, a busy timeout and short transactions. The explicit
0001 migration is frozen; subsequent releases require new revisions. PostgreSQL is an
unverified extension point. Workers drain and exit; no boot daemon is installed.
The dashboard binds to loopback. Hosted deployment requires a separate login identity,
ownership enforcement, quotas, upload controls and secure secret handling.
