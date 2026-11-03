import json
from pathlib import Path

import pytest
from conftest import FIXTURE
from streamlit.testing.v1 import AppTest
from typer.testing import CliRunner

from chesscoach.cli import app
from chesscoach.config import Settings, load_settings
from chesscoach.storage.db import Database
from chesscoach.sync import import_pgn


def test_cli_commands_and_persistence(monkeypatch, tmp_path):
    monkeypatch.setenv("CHESSCOACH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHESSCOACH_CONFIG", str(tmp_path / "config.yaml"))
    runner = CliRunner()
    assert runner.invoke(app, ["--help"]).exit_code == 0
    result = runner.invoke(app, ["import-pgn", "--file", str(FIXTURE), "--user", "alpha"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["new"] == 2
    result = runner.invoke(app, ["users", "list"])
    assert result.exit_code == 0 and "alpha" in result.output
    result = runner.invoke(app, ["progress", "--provider", "local", "--username", "alpha"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["overview"]["games"] == 2
    assert runner.invoke(app, ["config", "init"]).exit_code == 0
    assert runner.invoke(app, ["config", "init"]).exit_code == 1
    assert (
        runner.invoke(app, ["practice", "generate", "--provider", "local", "--username", "alpha"]).exit_code
        == 0
    )
    assert (
        runner.invoke(app, ["users", "link", "1", "--provider", "lichess", "--username", "alpha"]).exit_code
        == 0
    )
    result = runner.invoke(app, ["analyze", "--username", "missing", "--queue-only"])
    assert result.exit_code == 1 and "not registered" in result.output
    assert runner.invoke(app, ["worker", "--max-jobs", "0"]).exit_code == 2


def test_configuration_overrides_and_validation(monkeypatch, tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text("engine:\n  threads: 3\nproviders:\n  default: chess.com\n")
    monkeypatch.setenv("CHESSCOACH_CONFIG", str(config))
    monkeypatch.setenv("STOCKFISH_PATH", "/configured/engine")
    result = load_settings()
    assert result.engine.threads == 3 and result.engine.path == "/configured/engine"
    with pytest.raises(ValueError):
        Settings.model_validate({"engine": {"threads": 0}})


def test_dashboard_empty_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CHESSCOACH_DATA_DIR", str(tmp_path))
    script = Path(__file__).parents[1] / "src/chesscoach/dashboard/app.py"
    app_test = AppTest.from_file(str(script)).run(timeout=30)
    assert not app_test.exception
    assert "Your next move" in app_test.title[0].value


def test_dashboard_pages_render_player_data(monkeypatch, tmp_path):
    monkeypatch.setenv("CHESSCOACH_DATA_DIR", str(tmp_path))
    db = Database(load_settings().database_url)
    db.migrate()
    import_pgn(db, FIXTURE, "alpha")
    script = Path(__file__).parents[1] / "src/chesscoach/dashboard/app.py"
    app_test = AppTest.from_file(str(script)).run(timeout=30)
    assert not app_test.exception
    assert app_test.metric[0].value == "2"
    pages = app_test.sidebar.radio[0].options
    for page in pages:
        app_test.sidebar.radio[0].set_value(page).run(timeout=30)
        assert not app_test.exception, (page, app_test.exception)


def test_dashboard_shows_background_analysis_and_refreshes_status(monkeypatch, tmp_path):
    from conftest import FAKE_CONFIG
    from sqlalchemy import select

    from chesscoach.jobs import enqueue
    from chesscoach.storage.models import Account, AnalysisJob

    monkeypatch.setenv("CHESSCOACH_DATA_DIR", str(tmp_path))
    db = Database(load_settings().database_url)
    db.migrate()
    account_id, _ = import_pgn(db, FIXTURE, "alpha")
    with db.session() as session:
        player_id = session.get(Account, account_id).player_id
    enqueue(db, player_id, FAKE_CONFIG, limit=1)
    script = Path(__file__).parents[1] / "src/chesscoach/dashboard/app.py"
    app_test = AppTest.from_file(str(script)).run(timeout=30)
    assert not app_test.exception
    assert any("Analysis in progress" in caption.value for caption in app_test.caption)
    with db.session() as session:
        session.scalar(select(AnalysisJob)).status = "cancelled"
    app_test.run(timeout=30)
    assert not app_test.exception
    assert not any("Analysis in progress" in caption.value for caption in app_test.caption)
