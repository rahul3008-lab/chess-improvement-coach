from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


class Database:
    def __init__(self, url: str):
        sqlite = url.startswith("sqlite")
        self.engine = create_engine(
            url, connect_args={"check_same_thread": False, "timeout": 30} if sqlite else {}
        )
        if sqlite:

            @event.listens_for(self.engine, "connect")
            def setup(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=30000")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self):
        cfg = Config()
        cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")

    @contextmanager
    def session(self):
        with self.sessions.begin() as session:
            yield session
