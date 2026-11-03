from __future__ import annotations

import os
from pathlib import Path

import yaml
from platformdirs import user_config_path, user_data_path
from pydantic import BaseModel, Field


class DatabaseSettings(BaseModel):
    url: str | None = None


class ProviderSettings(BaseModel):
    default: str = "chesscom"
    historical_revalidate_days: int = Field(30, ge=0)
    request_interval: float = Field(0.25, ge=0)
    timeout: float = Field(30, gt=0)
    retries: int = Field(3, ge=0, le=10)


class EngineSettings(BaseModel):
    path: str = "stockfish"
    threads: int = Field(2, ge=1)
    hash_mb: int = Field(256, ge=1)
    multipv: int = Field(2, ge=1, le=10)


class AnalysisSettings(BaseModel):
    default_profile: str = "fast"
    fast_depth: int = Field(12, ge=1, le=50)
    deep_depth: int = Field(18, ge=1, le=50)
    seconds: float | None = Field(0.15, gt=0)
    deep_seconds: float | None = Field(2.0, gt=0)
    workers: int = Field(1, ge=1, le=16)
    lease_seconds: int = Field(900, ge=30)
    enable_cache: bool = True


class TrainingSettings(BaseModel):
    daily_minutes: int = Field(45, ge=1, le=480)
    spaced_repetition: bool = True


class Settings(BaseModel):
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    engine: EngineSettings = Field(default_factory=EngineSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    training: TrainingSettings = Field(default_factory=TrainingSettings)

    @property
    def data_dir(self) -> Path:
        return Path(os.environ.get("CHESSCOACH_DATA_DIR", user_data_path("chesscoach", appauthor=False)))

    @property
    def database_url(self) -> str:
        override = os.environ.get("CHESSCOACH_DATABASE_URL") or self.database.url
        if override:
            return override
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'chesscoach.db'}"


def config_path() -> Path:
    return Path(
        os.environ.get("CHESSCOACH_CONFIG", user_config_path("chesscoach", appauthor=False) / "config.yaml")
    )


def load_settings() -> Settings:
    path = config_path()
    settings = (
        Settings.model_validate(yaml.safe_load(path.read_text()) or {}) if path.exists() else Settings()
    )
    if os.environ.get("STOCKFISH_PATH"):
        settings.engine.path = os.environ["STOCKFISH_PATH"]
    return settings
