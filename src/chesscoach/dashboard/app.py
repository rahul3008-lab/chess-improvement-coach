from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import chess
import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from chesscoach.coach import explain_position
from chesscoach.config import config_path, load_settings
from chesscoach.dashboard.widgets import board_widget
from chesscoach.engines import Stockfish
from chesscoach.identity import resolve_account
from chesscoach.jobs import enqueue
from chesscoach.pgn import parse_games
from chesscoach.practice import (
    begin_session,
    finish_session,
    generate,
    next_exercises,
    source_board,
    submit_answer,
    tablebase_moves,
)
from chesscoach.providers import canonical
from chesscoach.reports import evaluation_rows, overview, period_report, profile_counts, refresh_coaching
from chesscoach.storage.db import Database
from chesscoach.storage.models import (
    Account,
    AccountGame,
    AnalysisJob,
    Exercise,
    Game,
    Goal,
    Player,
    PracticeSession,
    Recommendation,
    SyncRun,
    Weakness,
)
from chesscoach.sync import import_pgn

st.set_page_config(page_title="Chess Improvement Coach", page_icon="♞", layout="wide")
st.markdown(
    """<style>
.stApp{background:#0e1722;color:#edf0f4}section[data-testid="stSidebar"]{background:#14202e}
.block-container{padding-top:2.3rem;max-width:1500px}h1{font-family:Georgia,serif;font-weight:500!important;letter-spacing:-.04em}
h2,h3{letter-spacing:-.025em}div[data-testid="stMetric"]{background:#172433;border:1px solid #293a4d;border-radius:12px;padding:20px}
div[data-testid="stMetricLabel"]{color:#aebdce}div[data-testid="stMetricValue"]{color:#ebbc6e}
div[data-testid="stForm"]{background:#14202e;border-color:#293a4d;border-radius:12px}
.eyebrow{color:#d9ae67;font-size:12px;letter-spacing:.18em;text-transform:uppercase;margin-bottom:8px}
.subtitle{color:#9dadbf;font-size:17px;margin-top:-10px;margin-bottom:30px}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{background:#e5b567;color:#15202c;border:0}
</style>""",
    unsafe_allow_html=True,
)


@st.cache_resource
def database(url):
    db = Database(url)
    db.migrate()
    return db


settings = load_settings()
db = database(settings.database_url)


def title(name, description):
    st.markdown(
        '<div class="eyebrow">Chess Improvement Coach / Your study room</div>', unsafe_allow_html=True
    )
    st.title(name)
    st.markdown(f'<div class="subtitle">{description}</div>', unsafe_allow_html=True)


def task(command):
    logs = settings.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "dashboard-tasks.log").open("a") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "chesscoach", *command], stdout=log, stderr=log, start_new_session=True
        )
    st.session_state.setdefault("tasks", []).append(
        {"pid": process.pid, "command": command, "process": process}
    )
    st.success(f"Started local task (PID {process.pid}). Refresh to see new data.")


def import_panel(owner=None):
    st.subheader("Connect your games")
    with st.form("sync_games"):
        username = st.text_input("Chess.com username")
        attach = st.checkbox("Link to the selected player", value=owner is not None, disabled=owner is None)
        submitted = st.form_submit_button("Sync public games", type="primary")
        if submitted:
            if username.strip():
                command = ["sync", "--provider", "chess.com", "--username", username.strip()]
                if attach and owner:
                    command += ["--player-id", str(owner)]
                task(command)
            else:
                st.error("Enter a Chess.com username.")
    with st.form("pgn_upload"):
        upload = st.file_uploader(
            "Or import a PGN", type=["pgn"], help="Games are stored locally. Uploads are never published."
        )
        pgn_name = st.text_input("Your name as it appears in the PGN")
        link = st.checkbox(
            "Link local games to selected player", value=owner is not None, disabled=owner is None
        )
        if st.form_submit_button("Import PGN") and upload and pgn_name:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / Path(upload.name).name
                path.write_bytes(upload.getvalue())
                try:
                    _, result = import_pgn(db, path, pgn_name, owner if link else None)
                    st.success(f"Imported {result.new} new games; {result.skipped} unchanged games.")
                except ValueError as error:
                    st.error(str(error))
    for item in st.session_state.get("tasks", []):
        status = item["process"].poll()
        st.caption(
            f"PID {item['pid']} · {'Running' if status is None else 'Completed' if status == 0 else 'Failed — see task log'}"
        )
    log_path = settings.data_dir / "logs" / "dashboard-tasks.log"
    if log_path.exists():
        with st.expander("Local task output"):
            st.code(log_path.read_text(errors="replace")[-10000:])
    if st.button("Refresh profiles and data"):
        st.rerun()


with db.session() as session:
    players = list(session.scalars(select(Player).order_by(Player.id)))

st.sidebar.markdown("## ♞  Chess Coach")
st.sidebar.caption("A little better, every move.")
if not players:
    title(
        "Your next move starts here.",
        "Bring your games. Find your patterns. Build a deliberate practice habit.",
    )
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        st.markdown("### A study room built around your chess")
        st.write(
            "Your own games become the starting point: objective Stockfish analysis, specific positions to revisit, and a practice history that stays with you."
        )
        for label, text in (
            ("01 · Collect", "Sync public Chess.com archives or import tournament PGNs."),
            ("02 · Understand", "Find recurring losses with traceable engine evidence."),
            ("03 · Practise", "Revisit meaningful positions with spaced repetition."),
        ):
            st.subheader(label)
            st.write(text)
        st.info("Local multi-profile application. Profile selection is a convenience, not authentication.")
    with right:
        import_panel()
    st.stop()

initial_username = os.getenv("CHESSCOACH_INITIAL_USERNAME")
initial_provider = os.getenv("CHESSCOACH_INITIAL_PROVIDER")
initial_id = None
if initial_username:
    with db.session() as session:
        initial_id = session.scalar(
            select(Account.player_id).where(
                Account.username == initial_username.lower(), Account.provider == initial_provider
            )
        )
index = next((i for i, p in enumerate(players) if p.id == initial_id), 0)
owner = st.sidebar.selectbox(
    "Player",
    [p.id for p in players],
    index=index,
    format_func=lambda value: next(f"{p.display_name} · #{p.id}" for p in players if p.id == value),
)
player = next(p for p in players if p.id == owner)
with db.session() as session:
    accounts = list(session.scalars(select(Account).where(Account.player_id == owner)))
account_filter = st.sidebar.selectbox(
    "Accounts",
    [None] + [a.id for a in accounts],
    format_func=lambda value: (
        "All linked accounts"
        if value is None
        else next(f"{a.provider} / {a.username}" for a in accounts if a.id == value)
    ),
)
pages = [
    "Player Overview",
    "Rating Progress",
    "Game Explorer",
    "Opening Analysis",
    "Tactical Weaknesses",
    "Middlegame Analysis",
    "Endgame Analysis",
    "Practice Playground",
    "Recommended Learning",
    "Training Calendar",
    "Performance Reports",
    "Road to GM",
    "Import and Synchronisation",
    "Settings",
]
page = st.sidebar.radio("Study room", pages)
st.sidebar.divider()
time_filter = st.sidebar.selectbox(
    "Time control",
    [None, "rapid", "blitz", "bullet", "daily", "classical"],
    format_func=lambda v: v or "All time controls",
)
color_filter = st.sidebar.selectbox(
    "Colour", [None, "white", "black"], format_func=lambda v: v or "Both colours"
)
days_filter = st.sidebar.selectbox(
    "Period", [None, 7, 30, 90, 365], format_func=lambda v: f"Last {v} days" if v else "All time"
)
with st.sidebar.expander("Opening and rating filters"):
    opening_filter = st.text_input("ECO or opening name")
    rating_bounds = st.slider("Online rating range", 0, 4000, (0, 4000), 50)
filters = dict(
    account_id=account_filter,
    time_class=time_filter,
    color=color_filter,
    days=days_filter,
    opening=opening_filter or None,
    rating_min=rating_bounds[0] or None,
    rating_max=rating_bounds[1] if rating_bounds[1] < 4000 else None,
)
analytic_pages = {
    "Player Overview",
    "Rating Progress",
    "Game Explorer",
    "Opening Analysis",
    "Tactical Weaknesses",
    "Middlegame Analysis",
    "Endgame Analysis",
}
data = overview(db, owner, **filters) if page in analytic_pages else profile_counts(db, owner)
st.sidebar.caption(f"{data['analysed_games']} analysed · {data['unanalysed_games']} awaiting analysis")
if st.sidebar.button("Refresh data"):
    st.rerun()


@st.fragment(run_every="20s")
def watch_analysis():
    """Refresh visible statistics as independent analysis workers commit results."""
    with db.session() as session:
        counts = dict(
            session.execute(
                select(AnalysisJob.status, func.count())
                .where(
                    AnalysisJob.game_id.in_(
                        select(AccountGame.game_id).join(Account).where(Account.player_id == owner)
                    )
                )
                .group_by(AnalysisJob.status)
            ).all()
        )
    signature = tuple(sorted(counts.items()))
    key = f"analysis_job_signature_{owner}"
    previous = st.session_state.get(key)
    st.session_state[key] = signature
    if previous is not None and previous != signature and page != "Practice Playground":
        st.rerun()
    outstanding = counts.get("pending", 0) + counts.get("running", 0)
    if outstanding:
        st.caption(
            f"Analysis in progress · {counts.get('running', 0)} running · "
            f"{counts.get('pending', 0)} queued. This page refreshes automatically."
        )
    if counts.get("failed", 0):
        st.warning(f"{counts['failed']} analysis job(s) need retry. See chesscoach jobs list for details.")


watch_analysis()


def diagnosis(phase=None):
    with db.session() as session:
        _, _, rows = evaluation_rows(session, owner, **filters)
    rows = [
        (v, g, a, source) for v, g, a, source in rows if v.loss >= 100 and (phase is None or v.phase == phase)
    ]
    if not rows:
        st.info("No matching engine-verified losses yet. Analyse games or broaden the filters.")
        return
    records = [
        {
            "game": g.id,
            "ply": v.ply,
            "loss_cp": v.loss,
            "phase": v.phase,
            "depth": v.depth,
            "played": v.played_uci,
            "preferred": v.best_uci,
            "account": f"{source.provider}/{source.username}",
            "clock_seconds": v.clock,
        }
        for v, g, _, source in rows
    ]
    st.dataframe(records, hide_index=True, width="stretch")
    selected = st.selectbox(
        "Inspect a position",
        range(len(rows)),
        format_func=lambda i: f"Game {rows[i][1].id} · ply {rows[i][0].ply} · {rows[i][0].loss}cp",
    )
    value, game, link, account = rows[selected]
    left, right = st.columns([1, 1])
    with left:
        board_widget(chess.Board(value.fen), f"diagnosis_{owner}_{value.id}", link.color, disabled=True)
    with right:
        st.subheader("Evidence, then interpretation")
        st.write(explain_position(value))
        st.code(" ".join(value.pv))
        st.caption(
            f"Source game #{game.id} · {account.provider}/{account.username}. Conceptual labels require coach review."
        )
        if game.url and game.url.startswith("https://"):
            st.link_button("Open source game", game.url)


def rerun_practice():
    from streamlit.errors import StreamlitAPIException

    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()  # Initial full-page execution / Streamlit AppTest.


@st.fragment
def playground():
    mode_label = st.selectbox(
        "Practice mode",
        [
            "Mistakes from my games",
            "Tactical trainer",
            "Endgame trainer",
            "Play against engine",
            "Critical-move training",
            "Opening training",
            "Calculation training",
        ],
    )
    modes = {
        "Mistakes from my games": "mistakes",
        "Tactical trainer": "tactics",
        "Endgame trainer": "endgame",
        "Critical-move training": "critical",
        "Opening training": "opening",
        "Calculation training": "calculation",
    }
    if mode_label == "Play against engine":
        key = f"engine_{owner}"
        left, right = st.columns([1.2, 1])
        with right:
            skill = st.slider("Stockfish skill", 0, 20, 5)
            initial_fen = st.text_input("Starting FEN", chess.STARTING_FEN)
            if st.button("Start / reset position"):
                try:
                    board = chess.Board(initial_fen)
                    if not board.is_valid():
                        raise ValueError("Invalid chess position.")
                    st.session_state[key] = board
                    opponent = st.session_state.pop(f"{key}_opponent", None)
                    if opponent:
                        opponent.close()
                    rerun_practice()
                except ValueError as error:
                    st.error(str(error))
            st.caption("Click a piece, then its destination. Illegal moves leave the board unchanged.")
        board = st.session_state.setdefault(key, chess.Board())
        with left:
            move = board_widget(board, f"engine_board_{owner}", thinking=True)
        if move:
            try:
                # Keep the opponent alive between turns; stage the board until the reply succeeds.
                opponent_key = f"{key}_opponent"
                if opponent_key not in st.session_state:
                    st.session_state[opponent_key] = Stockfish(settings)
                trial = board.copy()
                trial.push(move)
                reply = st.session_state[opponent_key].play(trial, skill=skill, seconds=0.2)
                if reply:
                    trial.push(reply)
                st.session_state[key] = trial
                st.session_state.pop(f"{key}_error", None)
            except (ValueError, OSError, chess.engine.EngineError) as error:
                st.session_state[f"{key}_error"] = str(error)
                opponent = st.session_state.pop(f"{key}_opponent", None)
                if opponent:
                    try:
                        opponent.close()
                    except (OSError, chess.engine.EngineError):
                        pass
            rerun_practice()
        if st.session_state.get(f"{key}_error"):
            st.error(st.session_state[f"{key}_error"])
        if board.is_game_over():
            st.success(f"Game finished: {board.result()}")
        st.code(board.fen())
        return
    mode = modes[mode_label]
    if st.button("Generate exercises from my analysis"):
        st.success(f"Created {generate(db, owner, mode)} exercises.")
    include_future = st.checkbox("Include exercises scheduled for later")
    position_key = f"practice_position_{owner}_{mode}"
    exercises = next_exercises(
        db, owner, mode, due_only=not include_future, include_id=st.session_state.get(position_key)
    )
    if not exercises:
        st.info(
            "No exercises are due. Analyse games, generate exercises, or include future reviews. Ambiguous engine choices are excluded."
        )
        if mode == "endgame":
            st.caption("You can also practise a custom endgame FEN in Play against engine.")
        return
    by_id = {exercise.id: exercise for exercise in exercises}
    exercise_id = st.selectbox(
        "Position",
        [e.id for e in exercises],
        format_func=lambda value: f"#{value} · game {by_id[value].game_id} · {by_id[value].topic}",
        key=position_key,
    )
    exercise = by_id[exercise_id]
    key = f"practice_{owner}_{mode}_{exercise_id}"
    if key not in st.session_state:
        initial_board = source_board(db, owner, exercise_id)
        st.session_state[key] = {
            "board": initial_board.copy(),
            "initial_board": initial_board,
            "started": time.monotonic(),
            "hints": 0,
            "session_id": begin_session(db, owner, mode),
            "submitted": False,
            "feedback": None,
        }
    state = st.session_state[key]
    left, right = st.columns([1.2, 1], gap="large")
    with left:
        move = board_widget(
            state["board"],
            f"exercise_board_{key}",
            "white" if state["initial_board"].turn else "black",
            feedback=state["feedback"],
        )
    with right:
        st.subheader("Find the engine's preferred continuation")
        st.caption("Solutions reflect the recorded search depth. Other reasonable moves may exist.")
        st.write(f"Source: game #{exercise.game_id} · {exercise.topic}")
        if mode == "calculation":
            st.info(
                f"Calculate at least {min(5, len(exercise.solution))} plies before submitting. Board moves are a sandbox."
            )
            variation = st.text_input("Variation (SAN or UCI, separated by spaces)")
            if st.button("Submit calculation", disabled=state["submitted"]):
                try:
                    submit_answer(
                        db, owner, exercise, state, variation.split(), settings.training.spaced_repetition
                    )
                    rerun_practice()
                except ValueError as error:
                    st.error(str(error))
        if st.button("Hint", disabled=state["submitted"]):
            state["hints"] += 1
            st.info(f"Consider the piece on {exercise.solution[0][:2]}.")
        if st.button("Reveal continuation"):
            state["hints"] += 1
            state["revealed"] = True
        if state.get("revealed"):
            st.code(" ".join(exercise.solution))
        if state["submitted"]:
            st.success("Correct! You found the preferred continuation.")
            st.code(" ".join(exercise.solution))
        elif state["feedback"] == "wrong":
            st.error("Wrong answer. Try again.")
        next_id = next((item.id for item in exercises if item.id != exercise_id), None)
        if next_id is not None:
            st.button("Next position", on_click=lambda: st.session_state.update({position_key: next_id}))
        if st.button("Retry this position"):
            if not state["submitted"]:
                finish_session(db, owner, state["session_id"])
            del st.session_state[key]
            rerun_practice()
        st.caption(
            "Click a piece, then its destination. Wrong answers reset the position so you can try again."
        )
        if mode == "endgame":
            table_path = st.text_input("Optional local Syzygy directory")
            if st.button("Probe local tablebases") and table_path:
                try:
                    st.dataframe(tablebase_moves(state["board"], table_path))
                except (KeyError, OSError, ValueError) as error:
                    st.error(f"Tablebase unavailable for this position: {error}")
    if move:
        if not state["submitted"] and mode != "calculation":
            submit_answer(db, owner, exercise, state, [move.uci()], settings.training.spaced_repetition)
        else:
            state["board"].push(move)
        rerun_practice()


title(page, f"{player.display_name}'s study room · Built from your games, at your pace.")
if page == "Player Overview":
    columns = st.columns(4)
    for column, label, value in zip(
        columns,
        ["Games collected", "Games analysed", "Blunders / analysed game", "Practice accuracy"],
        [
            data["games"],
            data["analysed_games"],
            data["blunders_per_analysed_game"] if data["analysed_games"] else "—",
            f"{data['practice_accuracy']:.0%}" if data["practice_accuracy"] is not None else "—",
        ],
    ):
        column.metric(label, value)
    st.caption(data["sample_note"])
    left, right = st.columns([1.4, 1], gap="large")
    with left:
        st.subheader("Your chess, by time control")
        if data["time_controls"]:
            st.bar_chart(pd.DataFrame(data["time_controls"]).T.fillna(0))
        else:
            st.info("Import games to start your profile.")
        st.subheader("Recent games")
        st.dataframe(data["game_rows"][-10:][::-1], hide_index=True, width="stretch")
    with right:
        st.subheader("A focus for your next session")
        with db.session() as session:
            recs = session.scalars(select(Recommendation).where(Recommendation.player_id == owner)).all()
        for rec in recs:
            st.markdown(f"**{rec.topic.title()} · {rec.daily_minutes} min/day**")
            st.write(rec.rationale)
        if not recs:
            st.info("Analyse a sample of your games to get evidence-based study suggestions.")
        st.subheader("Linked accounts")
        for account in accounts:
            st.write(f"{account.provider} / {account.username}")
            st.caption(f"Last sync (UTC): {account.last_sync or 'Never'}")
            with db.session() as session:
                latest = session.scalar(
                    select(SyncRun)
                    .where(SyncRun.account_id == account.id)
                    .order_by(SyncRun.id.desc())
                    .limit(1)
                )
            if latest:
                st.caption(f"Latest attempt: {latest.status} · {latest.finished_at or latest.started_at} UTC")
                if latest.counts.get("unavailable_archives"):
                    st.warning(
                        f"{len(latest.counts['unavailable_archives'])} archive(s) unavailable. Stored games remain available."
                    )
        st.json(data["jobs"], expanded=False)

elif page == "Rating Progress":
    st.caption(
        "Online provider ratings remain separate from FIDE ratings. Each account and time control has its own series."
    )
    if data["ratings"]:
        ratings = pd.DataFrame(data["ratings"])
        ratings["date"] = pd.to_datetime(ratings["date"])
        st.line_chart(ratings, x="date", y="rating", color="series")
        st.dataframe(ratings, hide_index=True)
    else:
        st.info("No rating records match these filters.")

elif page == "Game Explorer":
    if not data["game_rows"]:
        st.info("No games match these filters.")
    else:
        ids = list(dict.fromkeys(row["id"] for row in reversed(data["game_rows"])))
        game_id = st.selectbox(
            "Game",
            ids,
            format_func=lambda value: next(
                f"#{row['id']} · {row['white']} – {row['black']} · {row['time_class']}"
                for row in data["game_rows"]
                if row["id"] == value
            ),
        )
        with db.session() as session:
            game = session.get(Game, game_id)
        parsed = next(parse_games(game.pgn))
        moves = list(parsed.mainline_moves())
        ply = st.slider("Replay position (ply)", 0, max(1, len(moves)), 0, key=f"replay_{game_id}")
        board = parsed.board()
        for move in moves[:ply]:
            board.push(move)
        left, right = st.columns([1.2, 1])
        with left:
            board_widget(board, f"replay_board_{owner}_{game_id}", disabled=True)
        with right:
            st.json(game.headers)
            st.download_button("Download PGN", game.pgn, file_name=f"game-{game_id}.pgn")
            st.code(board.fen())

elif page == "Opening Analysis":
    st.caption("PGN ECO/opening tags are used as supplied. Untagged positions are not guessed.")
    records = [
        {
            "opening": key,
            **value,
            "games": sum(value.values()),
            "win_rate": value.get("win", 0) / sum(value.values()),
        }
        for key, value in data["openings"].items()
    ]
    st.dataframe(records, hide_index=True, width="stretch")
    diagnosis("opening")

elif page in {"Tactical Weaknesses", "Middlegame Analysis", "Endgame Analysis"}:
    st.caption(
        "Engine losses are measured observations. Tactical or positional root causes need position review."
    )
    diagnosis({"Middlegame Analysis": "middlegame", "Endgame Analysis": "endgame"}.get(page))

elif page == "Practice Playground":
    playground()

elif page == "Recommended Learning":
    st.caption(
        "Recommendations and practice history cover the whole selected player. Sidebar game filters apply to analytical pages."
    )
    if st.button("Refresh recommendations"):
        refresh_coaching(db, owner, settings.training.daily_minutes)
    with db.session() as session:
        recs = session.scalars(select(Recommendation).where(Recommendation.player_id == owner)).all()
        weaknesses = session.scalars(
            select(Weakness).where(Weakness.player_id == owner, Weakness.scope == "all")
        ).all()
    for rec in recs:
        with st.container(border=True):
            st.subheader(f"{rec.topic.title()} · {rec.daily_minutes} minutes daily")
            st.write(rec.rationale)
            resource = rec.resource
            if resource.get("url"):
                st.link_button(resource["title"], resource["url"])
            st.caption(f"{resource['difficulty']} · {resource['licence']}")
            plan = resource.get("curriculum", {})
            if plan:
                with st.expander("Study sequence"):
                    st.caption(plan["level_note"])
                    st.write(plan["goal_activity"])
                    for module in plan["modules"]:
                        st.markdown(f"**{module['title']}** · {module['difficulty']}")
                        st.write(module["activity"])
    if not recs:
        st.info("No recommendations yet. Analyse games first.")
    with st.expander("Evidence and uncertainty"):
        st.json(
            [
                {
                    "topic": w.topic,
                    "occurrences": w.occurrences,
                    "confidence": w.confidence,
                    "evidence": w.evidence,
                }
                for w in weaknesses
            ]
        )

elif page == "Training Calendar":
    with db.session() as session:
        exercises = session.scalars(
            select(Exercise)
            .where(Exercise.player_id == owner, Exercise.active.is_(True))
            .order_by(Exercise.due_at)
        ).all()
        history = session.scalars(
            select(PracticeSession)
            .where(PracticeSession.player_id == owner)
            .order_by(PracticeSession.id.desc())
            .limit(30)
        ).all()
    st.subheader("Scheduled reviews (UTC)")
    st.dataframe(
        [
            {
                "due": e.due_at,
                "exercise": e.id,
                "mode": e.mode,
                "streak": e.streak,
                "interval_days": e.interval_days,
            }
            for e in exercises
        ],
        hide_index=True,
    )
    st.subheader("Session history")
    st.dataframe(
        [{"mode": s.mode, "started": s.started_at, "finished": s.finished_at} for s in history],
        hide_index=True,
    )

elif page == "Performance Reports":
    st.caption(
        "Period reports cover the whole player. Use the sidebar filters on analytical pages for specific slices."
    )
    days = st.selectbox("Report interval", [7, 30], format_func=lambda v: "Weekly" if v == 7 else "Monthly")
    report = period_report(db, owner, days)
    st.dataframe(report["periods"], hide_index=True)
    st.info(report["note"])
    if not report["comparison_supported"]:
        st.warning("At least 20 analysed games are needed in each period before comparing trends.")
    import json

    st.download_button("Export report JSON", json.dumps(report, indent=2), f"player-{owner}-report.json")

elif page == "Road to GM":
    st.write(
        "Build a long-term plan around tournament experience, deliberate practice and qualified coaching. Online ratings are not official FIDE achievements. This application does not predict or guarantee titles."
    )
    with db.session() as session:
        goals = session.scalars(select(Goal).where(Goal.player_id == owner)).all()
    for goal in goals:
        done = st.checkbox(goal.title, value=goal.completed, key=f"goal_{goal.id}")
        if done != goal.completed:
            with db.session() as session:
                session.get(Goal, goal.id).completed = done
    with st.form("goal_add"):
        goal_title = st.text_input("A concrete learning or tournament goal")
        if st.form_submit_button("Add goal") and goal_title.strip():
            with db.session() as session:
                session.add(Goal(player_id=owner, title=goal_title.strip()))
            st.rerun()
    st.subheader("Official over-the-board information")
    st.json(player.fide)

elif page == "Import and Synchronisation":
    import_panel(owner)
    with db.session() as session:
        recent_syncs = session.scalars(
            select(SyncRun)
            .join(Account)
            .where(Account.player_id == owner)
            .order_by(SyncRun.id.desc())
            .limit(10)
        ).all()
    with st.expander("Synchronisation history and unavailable archives"):
        st.json(
            [
                {
                    "account": run.account_id,
                    "status": run.status,
                    "started_utc": str(run.started_at),
                    "finished_utc": str(run.finished_at),
                    "counts": run.counts,
                    "error": run.error,
                }
                for run in recent_syncs
            ]
        )
    st.subheader("Engine analysis")
    profile = st.selectbox("Analysis profile", ["fast", "deep"])
    limit = st.number_input("Maximum new games to queue", min_value=1, max_value=100000, value=10)
    if st.button("Queue analysis and start worker", type="primary"):
        try:
            with Stockfish(settings, profile=profile) as engine:
                queued, fingerprint = enqueue(db, owner, engine.identity, limit=int(limit))
            task(["worker", "--player-id", str(owner), "--config-hash", fingerprint])
            st.info(f"Queued {queued} new jobs. Completed results are reused.")
        except (ValueError, OSError) as error:
            st.error(str(error))

elif page == "Settings":
    st.info(
        "Trusted local profiles share this computer's database. Authentication and remote deployment are not enabled."
    )
    with st.form("player_settings"):
        display_name = st.text_input("Display name", player.display_name)
        minutes = st.number_input(
            "Daily study minutes",
            1,
            480,
            int(player.preferences.get("daily_minutes", settings.training.daily_minutes)),
        )
        training_goals = ["general", "tournament", "title-development"]
        training_goal = st.selectbox(
            "Training goal",
            training_goals,
            index=training_goals.index(player.preferences.get("training_goal", "general")),
        )
        fide_id = st.text_input("FIDE ID (optional)", str(player.fide.get("id", "")))
        fide_rating = st.number_input(
            "Official FIDE standard rating (0 = not recorded)",
            0,
            4000,
            int(player.fide.get("standard_rating", 0)),
        )
        if st.form_submit_button("Save player preferences"):
            with db.session() as session:
                item = session.get(Player, owner)
                item.display_name = display_name.strip() or player.display_name
                item.preferences = {
                    **item.preferences,
                    "daily_minutes": minutes,
                    "training_goal": training_goal,
                }
                item.fide = {"id": fide_id, "standard_rating": fide_rating}
            st.success("Saved locally.")
    with st.form("link_account"):
        provider_name = st.text_input("Provider", "chess.com")
        external_name = st.text_input("Account username")
        if st.form_submit_button("Link account explicitly"):
            try:
                with db.session() as session:
                    resolve_account(session, canonical(provider_name), external_name, owner)
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    st.subheader("Local engine and storage")
    st.caption(f"Configuration: {config_path()}")
    st.json(settings.model_dump(exclude={"database"}))
    st.caption(
        "Edit the YAML configuration or set STOCKFISH_PATH to change the engine. Restart workers after changes."
    )
