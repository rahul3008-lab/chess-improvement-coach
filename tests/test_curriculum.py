from conftest import FAKE_CONFIG, FakeEngine
from sqlalchemy import select

from chesscoach.curriculum import CATALOGUE, curriculum
from chesscoach.jobs import enqueue, run_workers
from chesscoach.practice import generate, next_exercises
from chesscoach.reports import refresh_coaching
from chesscoach.storage.models import AnalysisJob, Player, Recommendation


def test_curriculum_has_prerequisites_and_explicit_licences():
    ids = {module.id for module in CATALOGUE}
    assert len(ids) >= 12
    assert all(set(module.prerequisites) <= ids and module.licence for module in CATALOGUE)
    assert curriculum("calculation", 900)["starting_level"] == "foundation"
    assert curriculum("calculation", 1900)["starting_level"] == "advanced"
    assert "slow practice" in curriculum("opening", 1400, "tournament")["goal_activity"]


def test_coaching_uses_individual_preferences(db, imported, settings):
    enqueue(db, imported, FAKE_CONFIG)
    run_workers(db, settings, imported, factory=lambda _: FakeEngine())
    with db.session() as session:
        session.get(Player, imported).preferences = {"daily_minutes": 20, "training_goal": "tournament"}
    refresh_coaching(db, imported)
    with db.session() as session:
        recommendations = session.scalars(
            select(Recommendation).where(Recommendation.player_id == imported)
        ).all()
        assert recommendations and sum(r.daily_minutes for r in recommendations) == 20
        assert all(r.resource["curriculum"]["goal"] == "tournament" for r in recommendations)


def test_superseded_exercise_leaves_due_queue(db, imported, settings):
    enqueue(db, imported, FAKE_CONFIG)
    run_workers(db, settings, imported, factory=lambda _: FakeEngine())
    generate(db, imported)
    assert next_exercises(db, imported)
    with db.session() as session:
        for job in session.scalars(select(AnalysisJob)):
            job.content_hash = "superseded"
    assert next_exercises(db, imported) == []
