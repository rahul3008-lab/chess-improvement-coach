from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from sqlalchemy import select, update

from chesscoach import __version__
from chesscoach.config import config_path, load_settings
from chesscoach.engines import Stockfish
from chesscoach.identity import resolve_account
from chesscoach.jobs import enqueue, run_workers
from chesscoach.providers import ProviderRegistry, canonical
from chesscoach.reports import overview, period_report, refresh_coaching
from chesscoach.storage.db import Database
from chesscoach.storage.models import Account, AnalysisJob, Goal, Player
from chesscoach.sync import import_pgn, sync_account

app = typer.Typer(
    no_args_is_help=True,
    help="Local-first chess analysis and deliberate practice. Data stays on your computer.",
)
users = typer.Typer(help="Create players and explicitly link external accounts.")
practice_app = typer.Typer(help="Create and solve exercises grounded in your analysed games.")
jobs_app = typer.Typer(help="Inspect and control persistent analysis jobs.")
config_app = typer.Typer(help="Manage portable configuration.")
goals_app = typer.Typer(help="Track individual learning goals.")
for name, child in (
    ("users", users),
    ("practice", practice_app),
    ("jobs", jobs_app),
    ("config", config_app),
    ("goals", goals_app),
):
    app.add_typer(child, name=name)
console = Console()
Username = Annotated[str, typer.Option(help="Chess provider username (or local PGN player name).")]
Provider = Annotated[str | None, typer.Option(help="Provider ID or alias; defaults to configured provider.")]


def guarded(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, OSError, httpx.HTTPError) as error:
            console.print(f"Error: {error}", style="red", markup=False)
            raise typer.Exit(1) from error

    return wrapper


def context():
    settings = load_settings()
    db = Database(settings.database_url)
    db.migrate()
    return settings, db


def player_for(db, provider, username):
    provider = canonical(provider or load_settings().providers.default)
    with db.session() as session:
        account = session.scalar(
            select(Account).where(Account.provider == provider, Account.username == username.strip().lower())
        )
        if account is None:
            raise ValueError("Account not registered. Run sync, import-pgn, or users link first.")
        return account.player_id


def emit(value):
    console.print_json(json.dumps(value, default=str))


def queue_player(db, settings, player_id, depth=None, profile="fast", limit=None, retry=False, priority=0):
    with Stockfish(settings, depth, profile) as engine:
        return enqueue(db, player_id, engine.identity, limit, priority, retry)


def background_worker(settings, player_id, fingerprint):
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "worker.log").open("a") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "chesscoach",
                "worker",
                "--player-id",
                str(player_id),
                "--config-hash",
                fingerprint,
            ],
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
    return process.pid


@app.callback()
def main(version: Annotated[bool, typer.Option("--version", is_eager=True)] = False):
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
@guarded
def sync(
    username: Username,
    provider: Provider = None,
    player_id: Annotated[int | None, typer.Option(help="Link a new account to this existing player.")] = None,
    revalidate: Annotated[bool, typer.Option(help="Revalidate historical archives immediately.")] = False,
):
    """Download only new/changed games. Does not require Stockfish."""
    settings, db = context()
    adapter = ProviderRegistry().get(provider or settings.providers.default, settings=settings.providers)
    try:
        account_id, result = sync_account(db, adapter, username, settings.providers, player_id, revalidate)
    finally:
        adapter.close()
    with db.session() as session:
        owner = session.get(Account, account_id).player_id
    refresh_coaching(db, owner, settings.training.daily_minutes)
    emit({"account_id": account_id, "player_id": owner, **asdict(result)})


@app.command()
@guarded
def analyze(
    username: Username,
    provider: Provider = None,
    depth: Annotated[int | None, typer.Option(min=1, max=50)] = None,
    profile: Annotated[str | None, typer.Option(help="fast or deep; defaults to configuration")] = None,
    limit: Annotated[
        int | None, typer.Option(min=1, help="Maximum new jobs to queue and jobs to process.")
    ] = None,
    workers: Annotated[int | None, typer.Option(min=1, max=16)] = None,
    queue_only: bool = False,
    background: bool = False,
    retry: bool = False,
    priority: int = 0,
):
    """Queue current-content games and analyse them; completed configurations are reused."""
    settings, db = context()
    profile = profile or settings.analysis.default_profile
    if profile not in {"fast", "deep"}:
        raise ValueError("Profile must be fast or deep.")
    owner = player_for(db, provider, username)
    queued, fingerprint = queue_player(db, settings, owner, depth, profile, limit, retry, priority)
    output = {"queued": queued, "config_hash": fingerprint}
    if background:
        output["worker_pid"] = background_worker(settings, owner, fingerprint)
    elif not queue_only:
        output.update(
            asdict(
                run_workers(
                    db,
                    settings,
                    owner,
                    fingerprint,
                    limit,
                    workers,
                    on_progress=lambda job, result: console.print(
                        f"Job {job}: completed={result.completed}, failed={result.failed}"
                    ),
                )
            )
        )
        refresh_coaching(db, owner, settings.training.daily_minutes)
    emit(output)
    if output.get("failed"):
        raise typer.Exit(1)


@guarded
def update_games(
    username: Username,
    provider: Provider = None,
    depth: Annotated[int | None, typer.Option(min=1, max=50)] = None,
    limit: Annotated[int | None, typer.Option(min=1)] = None,
    foreground: Annotated[
        bool, typer.Option(help="Wait for analysis instead of starting a background worker.")
    ] = False,
):
    """Incrementally sync, queue missing analysis, and refresh coaching."""
    sync(username, provider)
    analyze(username=username, provider=provider, depth=depth, limit=limit, background=not foreground)


# Public command spelling matches the documented workflow.
app.command("update")(update_games)


@app.command("import-pgn")
@guarded
def import_command(
    file: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    user: Annotated[str, typer.Option(help="Exact player name in the PGN White/Black headers.")],
    player_id: int | None = None,
):
    """Import local games with file provenance. Matching usernames never merge players automatically."""
    settings, db = context()
    account_id, result = import_pgn(db, file, user, player_id)
    with db.session() as session:
        owner = session.get(Account, account_id).player_id
    refresh_coaching(db, owner, settings.training.daily_minutes)
    emit({"account_id": account_id, "player_id": owner, **asdict(result)})


@app.command()
@guarded
def progress(
    username: Username, provider: Provider = None, days: Annotated[int, typer.Option(min=1, max=3650)] = 30
):
    """Show real stored statistics and compare adjacent periods."""
    _, db = context()
    owner = player_for(db, provider, username)
    report = overview(db, owner)
    report.pop("game_rows")
    report.pop("ratings")
    emit({"overview": report, "period_report": period_report(db, owner, days)})


@app.command()
@guarded
def worker(
    player_id: int | None = None,
    config_hash: str | None = None,
    workers: Annotated[int | None, typer.Option(min=1, max=16)] = None,
    max_jobs: Annotated[int | None, typer.Option(min=1)] = None,
):
    """Drain pending jobs, recover expired leases, then exit. Safe to run alongside the dashboard."""
    settings, db = context()
    result = run_workers(db, settings, player_id, config_hash, max_jobs, workers)
    with db.session() as session:
        owners = [player_id] if player_id is not None else list(session.scalars(select(Player.id)))
    for owner in owners:
        refresh_coaching(db, owner, settings.training.daily_minutes)
    emit(asdict(result))
    if result.failed:
        raise typer.Exit(1)


@users.command("add")
@guarded
def user_add(name: str):
    """Create an internal player without an external account."""
    _, db = context()
    with db.session() as session:
        if not name.strip():
            raise ValueError("Display name cannot be empty.")
        item = Player(display_name=name.strip())
        session.add(item)
        session.flush()
        emit({"player_id": item.id, "name": item.display_name})


@users.command("link")
@guarded
def user_link(player_id: int, username: Username, provider: Provider = None):
    """Explicitly associate an account with an existing player. Does not transfer an owned account."""
    settings, db = context()
    with db.session() as session:
        account = resolve_account(session, provider or settings.providers.default, username, player_id)
        emit({"account_id": account.id, "player_id": account.player_id})


@users.command("list")
@guarded
def user_list():
    _, db = context()
    with db.session() as session:
        emit(
            [
                {
                    "player_id": p.id,
                    "display_name": p.display_name,
                    "accounts": [
                        {
                            "id": a.id,
                            "provider": a.provider,
                            "username": a.username,
                            "last_sync_utc": a.last_sync,
                        }
                        for a in session.scalars(select(Account).where(Account.player_id == p.id))
                    ],
                }
                for p in session.scalars(select(Player))
            ]
        )


@practice_app.command("generate")
@guarded
def practice_generate(
    username: Username,
    provider: Provider = None,
    mode: str = "mistakes",
    limit: Annotated[int, typer.Option(min=1)] = 100,
):
    from chesscoach.practice import generate

    _, db = context()
    emit({"created": generate(db, player_for(db, provider, username), mode, limit)})


@practice_app.command("start")
@guarded
def practice_start(username: Username, provider: Provider = None, mode: str = "mistakes"):
    import chess

    from chesscoach.practice import begin_session, finish_session, next_exercises, record_attempt

    settings, db = context()
    owner = player_for(db, provider, username)
    exercises = next_exercises(db, owner, mode)
    if not exercises:
        typer.echo("No due exercises. Analyse games, then run practice generate (or choose another mode).")
        return
    session_id = begin_session(db, owner, mode)
    try:
        for exercise in exercises:
            typer.echo(str(chess.Board(exercise.fen)))
            typer.echo(f"FEN: {exercise.fen}")
            started = time.monotonic()
            answer = typer.prompt("Move (or space-separated variation for calculation); q to finish")
            if answer.lower() == "q":
                break
            correct = record_attempt(
                db,
                owner,
                session_id,
                exercise.id,
                answer.split(),
                time.monotonic() - started,
                spaced=settings.training.spaced_repetition,
            )
            typer.echo("Correct." if correct else f"Engine preferred: {' '.join(exercise.solution)}")
    finally:
        finish_session(db, owner, session_id)
    refresh_coaching(db, owner, settings.training.daily_minutes)


@jobs_app.command("list")
@guarded
def jobs_list(player_id: int | None = None):
    from chesscoach.jobs import game_ids_for_player

    _, db = context()
    with db.session() as session:
        query = select(AnalysisJob).order_by(AnalysisJob.id.desc()).limit(100)
        if player_id is not None:
            query = query.where(AnalysisJob.game_id.in_(game_ids_for_player(player_id)))
        emit(
            [
                {
                    "id": j.id,
                    "game_id": j.game_id,
                    "status": j.status,
                    "attempts": j.attempts,
                    "config_hash": j.config_hash,
                    "error": j.error,
                }
                for j in session.scalars(query)
            ]
        )


@jobs_app.command("cancel")
@guarded
def cancel_job(job_id: int):
    _, db = context()
    with db.session() as session:
        result = session.execute(
            update(AnalysisJob)
            .where(AnalysisJob.id == job_id, AnalysisJob.status.in_(["pending", "running"]))
            .values(status="cancelled", lease_token=None, lease_until=None)
        )
        emit({"cancelled": result.rowcount})


@jobs_app.command("retry")
@guarded
def retry_job(job_id: int):
    _, db = context()
    with db.session() as session:
        result = session.execute(
            update(AnalysisJob)
            .where(AnalysisJob.id == job_id, AnalysisJob.status.in_(["failed", "cancelled"]))
            .values(status="pending", error=None)
        )
        emit({"retried": result.rowcount})


@goals_app.command("add")
@guarded
def add_goal(player_id: int, title: str):
    _, db = context()
    with db.session() as session:
        if session.get(Player, player_id) is None:
            raise ValueError("Player does not exist.")
        session.add(Goal(player_id=player_id, title=title))


@config_app.command("init")
@guarded
def init_config():
    import yaml

    path = config_path()
    if path.exists():
        raise ValueError(f"Configuration already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(load_settings().model_dump()), encoding="utf-8")
    typer.echo(str(path))


@app.command()
@guarded
def doctor():
    """Check database migration, configuration paths, and local engine availability."""
    settings, db = context()
    result = {
        "version": __version__,
        "config": str(config_path()),
        "data_dir": str(settings.data_dir),
        "database": db.engine.url.render_as_string(hide_password=True),
    }
    try:
        with Stockfish(settings) as engine:
            result["engine"] = engine.identity
    except (ValueError, OSError) as error:
        result["engine_error"] = str(error)
    emit(result)


@app.command()
@guarded
def dashboard(
    username: str | None = None,
    provider: Provider = None,
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 8501,
):
    """Launch the local multi-profile dashboard on 127.0.0.1."""
    context()
    environment = os.environ.copy()
    if username:
        environment["CHESSCOACH_INITIAL_USERNAME"] = username
        environment["CHESSCOACH_INITIAL_PROVIDER"] = canonical(provider or load_settings().providers.default)
    script = Path(__file__).parent / "dashboard" / "app.py"
    raise typer.Exit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(script),
                "--server.address",
                "127.0.0.1",
                "--server.port",
                str(port),
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
                "--theme.base",
                "dark",
                "--theme.primaryColor",
                "#e5b567",
                "--theme.backgroundColor",
                "#0e1722",
                "--theme.secondaryBackgroundColor",
                "#172433",
                "--theme.textColor",
                "#edf0f4",
            ],
            env=environment,
        )
    )
