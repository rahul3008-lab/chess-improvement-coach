from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import delete, func, select

from chesscoach.curriculum import curriculum
from chesscoach.storage.models import (
    Account,
    AccountGame,
    AnalysisJob,
    Attempt,
    Game,
    MoveEvaluation,
    Player,
    PracticeSession,
    ProgressSnapshot,
    Recommendation,
    Weakness,
    now,
)

# Links only: third-party materials are neither copied nor represented as openly licensed.
RESOURCES = {
    "calculation": {
        "title": "Lichess practice: tactical patterns",
        "url": "https://lichess.org/practice",
        "licence": "External resource; see publisher terms",
        "difficulty": "beginner–advanced",
    },
    "opening": {
        "title": "Lichess opening explorer",
        "url": "https://lichess.org/analysis#explorer",
        "licence": "External resource; see publisher terms",
        "difficulty": "all levels",
    },
    "endgame": {
        "title": "Lichess practice: endgames",
        "url": "https://lichess.org/practice",
        "licence": "External resource; see publisher terms",
        "difficulty": "beginner–advanced",
    },
    "time management": {
        "title": "Review your clock annotations",
        "url": None,
        "licence": "Your own game data",
        "difficulty": "all levels",
    },
}


def game_rows(
    session,
    player_id,
    account_id=None,
    time_class=None,
    color=None,
    days=None,
    opening=None,
    rating_min=None,
    rating_max=None,
):
    query = (
        select(Game, AccountGame, Account)
        .select_from(Game)
        .join(AccountGame, AccountGame.game_id == Game.id)
        .join(Account, Account.id == AccountGame.account_id)
        .where(Account.player_id == player_id)
    )
    if account_id is not None:
        query = query.where(Account.id == account_id)
    if time_class:
        query = query.where(Game.time_class == time_class)
    if color:
        query = query.where(AccountGame.color == color)
    if days:
        query = query.where(Game.played_at >= now() - timedelta(days=days))
    if rating_min is not None:
        query = query.where(AccountGame.rating >= rating_min)
    if rating_max is not None:
        query = query.where(AccountGame.rating <= rating_max)
    rows = session.execute(query.order_by(Game.played_at, Game.id)).all()
    if opening:
        rows = [
            r
            for r in rows
            if opening.lower()
            in str(r[0].headers.get("ECO", "") + " " + r[0].headers.get("Opening", "")).lower()
        ]
    return rows


def latest_jobs(session, game_ids):
    """One latest completed, current-content result per game; old analyses remain in storage."""
    jobs = session.scalars(
        select(AnalysisJob)
        .join(Game)
        .where(
            AnalysisJob.game_id.in_(game_ids),
            AnalysisJob.status == "completed",
            AnalysisJob.content_hash == Game.content_hash,
        )
        .order_by(AnalysisJob.finished_at, AnalysisJob.id)
    ).all()
    return {job.game_id: job for job in jobs}


def evaluation_rows(session, player_id, **filters):
    games = game_rows(session, player_id, **filters)
    jobs = latest_jobs(session, [game.id for game, _, _ in games])
    by_job = defaultdict(list)
    if jobs:
        for value in session.scalars(
            select(MoveEvaluation).where(MoveEvaluation.job_id.in_([j.id for j in jobs.values()]))
        ):
            by_job[value.job_id].append(value)
    values = []
    seen = set()
    for game, link, account in games:
        job = jobs.get(game.id)
        for value in by_job[job.id] if job else []:
            # FEN stores the side to move explicitly; no need to reconstruct every historical board.
            turn = "white" if value.fen.split(" ", 2)[1] == "w" else "black"
            key = (value.id, turn)
            if turn == link.color and key not in seen:
                values.append((value, game, link, account))
                seen.add(key)
    return games, jobs, values


def overview(db, player_id, **filters):
    with db.session() as session:
        rows, jobs, evaluations = evaluation_rows(session, player_id, **filters)
        ids = {game.id for game, _, _ in rows}
        results = Counter()
        openings = defaultdict(lambda: Counter())
        time_controls = defaultdict(lambda: Counter())
        ratings = []
        games = []
        for game, link, account in rows:
            raw = game.headers.get("Result", "*")
            winner = "white" if raw == "1-0" else "black" if raw == "0-1" else None
            outcome = (
                "draw"
                if raw == "1/2-1/2"
                else "win"
                if winner == link.color
                else "loss"
                if winner
                else "unfinished"
            )
            results[outcome] += 1
            opening = (
                game.headers.get("ECO", "Unclassified")
                + " · "
                + game.headers.get("Opening", "Unknown opening")
            )
            openings[opening][outcome] += 1
            time_controls[game.time_class][outcome] += 1
            if link.rating is not None and game.played_at:
                ratings.append(
                    {
                        "date": game.played_at.isoformat(),
                        "rating": link.rating,
                        "series": f"{account.provider}/{account.username} · {game.time_class}",
                        "time_class": game.time_class,
                    }
                )
            games.append(
                {
                    "id": game.id,
                    "date": game.played_at.isoformat() if game.played_at else None,
                    "white": game.headers.get("White"),
                    "black": game.headers.get("Black"),
                    "result": outcome,
                    "color": link.color,
                    "rating": link.rating,
                    "time_class": game.time_class,
                    "opening": opening,
                    "provider": account.provider,
                    "account": account.username,
                    "analysed": game.id in jobs,
                    "url": game.url,
                }
            )
        pending = dict(
            session.execute(
                select(AnalysisJob.status, func.count())
                .where(AnalysisJob.game_id.in_(ids))
                .group_by(AnalysisJob.status)
            ).all()
        )
        attempts = (
            session.execute(
                select(Attempt).join(PracticeSession).where(PracticeSession.player_id == player_id)
            )
            .scalars()
            .all()
        )
        losses = [value.loss for value, *_ in evaluations]
        # Count unique games for totals, while results retain linked-account perspective for self-play.
        return {
            "player_id": player_id,
            "games": len(ids),
            "analysed_games": len(jobs),
            "results": dict(results),
            "unanalysed_games": len(ids - jobs.keys()),
            "jobs": pending,
            "mean_cp_loss": round(sum(losses) / len(losses), 1) if losses else None,
            "blunders": sum(value.label == "blunder" for value, *_ in evaluations),
            "blunders_per_analysed_game": round(
                sum(v.label == "blunder" for v, *_ in evaluations) / len(jobs), 2
            )
            if jobs
            else None,
            "evaluated_moves": len(evaluations),
            "practice_attempts": len(attempts),
            "practice_accuracy": round(sum(a.correct for a in attempts) / len(attempts), 3)
            if attempts
            else None,
            "unassisted_accuracy": round(sum(a.correct and a.hints == 0 for a in attempts) / len(attempts), 3)
            if attempts
            else None,
            "ratings": ratings,
            "openings": {k: dict(v) for k, v in openings.items()},
            "time_controls": {k: dict(v) for k, v in time_controls.items()},
            "game_rows": games,
            "sample_note": "Insufficient analysed games for a reliable trend (minimum 20)."
            if len(jobs) < 20
            else "Descriptive statistics; differences do not establish causation or future rating gains.",
        }


def profile_counts(db, player_id):
    """Small indexed queries for navigation/practice; never materialise game PGNs or evaluations."""
    ids = select(AccountGame.game_id).join(Account).where(Account.player_id == player_id)
    with db.session() as session:
        total = session.scalar(select(func.count()).select_from(Game).where(Game.id.in_(ids)))
        analysed = session.scalar(
            select(func.count(func.distinct(AnalysisJob.game_id)))
            .join(Game)
            .where(
                AnalysisJob.game_id.in_(ids),
                AnalysisJob.status == "completed",
                AnalysisJob.content_hash == Game.content_hash,
            )
        )
    return {"games": total, "analysed_games": analysed, "unanalysed_games": total - analysed}


def refresh_coaching(db, player_id, daily_minutes=45):
    metrics = overview(db, player_id)
    with db.session() as session:
        player = session.get(Player, player_id)
        if player is None:
            raise ValueError("Player does not exist.")
        daily_minutes = int(player.preferences.get("daily_minutes", daily_minutes))
        goal = player.preferences.get("training_goal", "general")
        ratings = [point["rating"] for point in metrics["ratings"][-20:]]
        rating = sorted(ratings)[len(ratings) // 2] if ratings else None
        _, _, values = evaluation_rows(session, player_id)
        buckets = defaultdict(list)
        for value, game, link, _ in values:
            if value.loss < 100:
                continue
            topic = (
                "opening"
                if value.phase == "opening"
                else "endgame"
                if value.phase == "endgame"
                else "calculation"
            )
            scope = f"{game.time_class}/{link.color}/{value.phase}"
            buckets[(topic, "all")].append(value)
            buckets[(topic, scope)].append(value)
            if value.clock is not None and value.clock <= 10:
                buckets[("time management", "all")].append(value)
        session.execute(delete(Weakness).where(Weakness.player_id == player_id))
        session.execute(delete(Recommendation).where(Recommendation.player_id == player_id))
        ranked = sorted(
            ((topic, vals) for (topic, scope), vals in buckets.items() if scope == "all"),
            key=lambda item: sum(min(v.loss, 1000) for v in item[1]),
            reverse=True,
        )
        for (topic, scope), vals in buckets.items():
            session.add(
                Weakness(
                    player_id=player_id,
                    topic=topic,
                    scope=scope,
                    occurrences=len(vals),
                    severity=sum(min(v.loss, 1000) for v in vals) / len(vals),
                    confidence="Engine loss verified; conceptual cause uncertain. Coach review recommended.",
                    evidence=[
                        {"evaluation_id": v.id, "job_id": v.job_id, "ply": v.ply, "loss": v.loss}
                        for v in vals[:30]
                    ],
                )
            )
        for index, (topic, vals) in enumerate(ranked[:3]):
            weight = [0.5, 0.3, 0.2][index] if len(ranked) >= 3 else 1 / min(2, len(ranked))
            session.add(
                Recommendation(
                    player_id=player_id,
                    topic=topic,
                    rationale=f"{len(vals)} engine-verified losses of at least 100cp in this category. "
                    "Review the cited positions before drawing conclusions about concepts.",
                    daily_minutes=max(1, round(daily_minutes * weight)),
                    resource={**RESOURCES[topic], "curriculum": curriculum(topic, rating, goal)},
                )
            )
        snapshot = {k: v for k, v in metrics.items() if k not in {"ratings", "game_rows", "openings"}}
        previous = session.scalar(
            select(ProgressSnapshot)
            .where(ProgressSnapshot.player_id == player_id)
            .order_by(ProgressSnapshot.id.desc())
            .limit(1)
        )
        if previous is None or previous.metrics != snapshot:
            session.add(ProgressSnapshot(player_id=player_id, metrics=snapshot))
    return metrics


def period_report(db, player_id, days=30):
    cutoff, earlier = now() - timedelta(days=days), now() - timedelta(days=2 * days)
    with db.session() as session:
        rows, jobs, values = evaluation_rows(session, player_id)
        reports = []
        for label, start, end in (("previous", earlier, cutoff), ("current", cutoff, now())):
            ids = {g.id for g, _, _ in rows if g.played_at and start <= g.played_at < end}
            losses = [v.loss for v, g, *_ in values if g.id in ids]
            analysed = len(ids & jobs.keys())
            reports.append(
                {
                    "period": label,
                    "games": len(ids),
                    "analysed_games": analysed,
                    "mean_cp_loss": round(sum(losses) / len(losses), 1) if losses else None,
                    "blunders_per_game": round(
                        sum(v.label == "blunder" for v, g, *_ in values if g.id in ids) / analysed, 2
                    )
                    if analysed
                    else None,
                }
            )
        return {
            "days_per_period": days,
            "periods": reports,
            "comparison_supported": all(report["analysed_games"] >= 20 for report in reports),
            "note": "Compare like time controls, colours, rating ranges and analysis settings; small samples are inconclusive.",
        }
