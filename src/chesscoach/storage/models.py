from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    """Naive UTC, consistent across SQLite and PostgreSQL storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=now)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    fide: Mapped[dict] = mapped_column(JSON, default=dict)


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("provider", "username"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    username: Mapped[str] = mapped_column(String(200))
    external_id: Mapped[str | None] = mapped_column(String(200))
    last_sync: Mapped[datetime | None] = mapped_column(DateTime)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (UniqueConstraint("provider", "source_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(Text)
    pgn: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    headers: Mapped[dict] = mapped_column(JSON)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    time_class: Mapped[str] = mapped_column(String(50), default="unknown")
    played_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class AccountGame(Base):
    __tablename__ = "account_games"
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), primary_key=True)
    color: Mapped[str] = mapped_column(String(8))
    rating: Mapped[int | None] = mapped_column(Integer)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)


class Archive(Base):
    __tablename__ = "archives"
    __table_args__ = (UniqueConstraint("account_id", "url"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    url: Mapped[str] = mapped_column(String(500))
    etag: Mapped[str | None] = mapped_column(String(300))
    last_modified: Mapped[str | None] = mapped_column(String(200))
    checksum: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(default=now)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    started_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running")
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (UniqueConstraint("game_id", "config_hash", "content_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    config: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    priority: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class MoveEvaluation(Base):
    __tablename__ = "move_evaluations"
    __table_args__ = (UniqueConstraint("job_id", "ply"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("analysis_jobs.id"), index=True)
    ply: Mapped[int] = mapped_column(Integer)
    fen: Mapped[str] = mapped_column(Text)
    played_uci: Mapped[str] = mapped_column(String(8))
    best_uci: Mapped[str | None] = mapped_column(String(8))
    cp_before: Mapped[int] = mapped_column(Integer)
    cp_after: Mapped[int] = mapped_column(Integer)
    mate_before: Mapped[int | None] = mapped_column(Integer)
    mate_after: Mapped[int | None] = mapped_column(Integer)
    loss: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(30))
    phase: Mapped[str] = mapped_column(String(30))
    pv: Mapped[list] = mapped_column(JSON)
    alternatives: Mapped[list] = mapped_column(JSON, default=list)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    clock: Mapped[float | None] = mapped_column(Float)


class PositionCache(Base):
    __tablename__ = "position_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Weakness(Base):
    __tablename__ = "weaknesses"
    __table_args__ = (UniqueConstraint("player_id", "topic", "scope"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    topic: Mapped[str] = mapped_column(String(100))
    scope: Mapped[str] = mapped_column(String(100), default="all")
    occurrences: Mapped[int] = mapped_column(Integer)
    severity: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(100))
    evidence: Mapped[list] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("player_id", "topic"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    topic: Mapped[str] = mapped_column(String(100))
    rationale: Mapped[str] = mapped_column(Text)
    daily_minutes: Mapped[int] = mapped_column(Integer)
    resource: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=now)


class Exercise(Base):
    __tablename__ = "exercises"
    __table_args__ = (UniqueConstraint("player_id", "evaluation_id", "mode"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("move_evaluations.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    mode: Mapped[str] = mapped_column(String(30))
    fen: Mapped[str] = mapped_column(Text)
    solution: Mapped[list] = mapped_column(JSON)
    topic: Mapped[str] = mapped_column(String(100))
    due_at: Mapped[datetime] = mapped_column(default=now, index=True)
    interval_days: Mapped[int] = mapped_column(default=0)
    streak: Mapped[int] = mapped_column(default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PracticeSession(Base):
    __tablename__ = "practice_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    mode: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("practice_sessions.id"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"))
    moves: Mapped[list] = mapped_column(JSON)
    correct: Mapped[bool] = mapped_column(Boolean)
    seconds: Mapped[float] = mapped_column(Float)
    hints: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=now)


class ProgressSnapshot(Base):
    __tablename__ = "progress_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
    metrics: Mapped[dict] = mapped_column(JSON)


class RatingPoint(Base):
    __tablename__ = "rating_history"
    __table_args__ = (UniqueConstraint("account_id", "game_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    time_class: Mapped[str] = mapped_column(String(50))
    rating: Mapped[int] = mapped_column(Integer)
    played_at: Mapped[datetime | None] = mapped_column(DateTime)


class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    target_date: Mapped[datetime | None] = mapped_column(DateTime)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
