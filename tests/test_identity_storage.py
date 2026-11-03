import pytest
from conftest import FIXTURE
from sqlalchemy import func, select

from chesscoach.identity import resolve_account
from chesscoach.providers import ProviderRegistry, canonical
from chesscoach.storage.models import Account, AccountGame, Game, Player
from chesscoach.sync import import_pgn


def test_same_username_different_providers_is_not_same_player(db):
    with db.session() as session:
        a = resolve_account(session, "chess.com", "Alpha")
        b = resolve_account(session, "lichess", "alpha")
        assert a.player_id != b.player_id
        assert a.provider == "chesscom"
        assert resolve_account(session, "chesscom", "ALPHA").id == a.id


def test_explicit_account_link(db):
    with db.session() as session:
        a = resolve_account(session, "chess.com", "alpha")
        b = resolve_account(session, "lichess", "beta", a.player_id)
        assert b.player_id == a.player_id
        other = resolve_account(session, "chess.com", "gamma")
        with pytest.raises(ValueError, match="already belongs"):
            resolve_account(session, "lichess", "beta", other.player_id)
        with pytest.raises(ValueError, match="does not exist"):
            resolve_account(session, "lichess", "new", 999)


def test_local_import_idempotent_and_provenance(db, tmp_path):
    _, first = import_pgn(db, FIXTURE, "alpha")
    path = tmp_path / "another-name.pgn"
    path.write_text(FIXTURE.read_text())
    _, second = import_pgn(db, path, "alpha")
    assert first.new == 2 and second.new == 0 and second.skipped == 2
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(Game)) == 2
        link = session.scalars(select(AccountGame)).first()
        assert link.provenance["sources"] == ["games.pgn", "another-name.pgn"]


def test_pgn_import_rolls_back_all_games_on_invalid_identity(db):
    with pytest.raises(ValueError, match="does not include"):
        import_pgn(db, FIXTURE, "not-in-the-games")
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(Player)) == 0
        assert session.scalar(select(func.count()).select_from(Game)) == 0


def test_independent_imports_preserve_player_separation(db, tmp_path):
    a, _ = import_pgn(db, FIXTURE, "alpha")
    path = tmp_path / "beta.pgn"
    path.write_text(FIXTURE.read_text().replace('"alpha"', '"beta"'))
    b, _ = import_pgn(db, path, "beta")
    with db.session() as session:
        assert session.get(Account, a).player_id != session.get(Account, b).player_id
        assert session.scalar(select(func.count()).select_from(Game)) == 4


def test_registry_and_plugin_extension():
    registry = ProviderRegistry()
    assert registry.get("chess.com").id == "chesscom"
    assert canonical("local-pgn") == "local"
    registry.register("example", lambda: "custom")
    assert registry.get("example") == "custom"
    with pytest.raises(ValueError, match="Unsupported"):
        registry.get("unsupported")


def test_migrations_are_idempotent(db):
    db.migrate()
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(Account)) == 0
