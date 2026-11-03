"""Original, freely reusable study outlines. External resources are links only."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    difficulty: str
    prerequisites: tuple[str, ...]
    activity: str
    topic: str
    licence: str = "GPL-3.0-or-later (original project text)"


CATALOGUE = [
    Module(
        "tactics-basic",
        "Tactical patterns",
        "foundation",
        (),
        "Before every move, list checks, captures and direct threats. Revisit three missed opportunities from your games.",
        "calculation",
    ),
    Module(
        "candidate-moves",
        "Candidate moves and calculation",
        "intermediate",
        ("tactics-basic",),
        "Choose three candidate moves. Calculate the strongest reply to each before checking the recorded engine line.",
        "calculation",
    ),
    Module(
        "tactics-advanced",
        "Advanced tactical calculation",
        "advanced",
        ("candidate-moves",),
        "Calculate a forcing line without moving the pieces. Write down the final position and compare with engine evidence.",
        "calculation",
    ),
    Module(
        "opening-principles",
        "Opening principles",
        "foundation",
        (),
        "Review development, central influence and king safety in your first twelve moves. Explain every pawn move.",
        "opening",
    ),
    Module(
        "opening-plans",
        "Opening-specific plans",
        "intermediate",
        ("opening-principles",),
        "Choose one recurring ECO family. Compare your positions, list common pawn breaks and rehearse the move order.",
        "opening",
    ),
    Module(
        "pawn-structures",
        "Pawn structures",
        "intermediate",
        ("opening-principles",),
        "Identify isolated, doubled and passed pawns in a game. Test a proposed pawn break against the engine.",
        "calculation",
    ),
    Module(
        "positional-strategy",
        "Piece coordination and exchanges",
        "advanced",
        ("pawn-structures",),
        "Find your least active piece. Compare two improving plans and evaluate exchanges from both sides.",
        "calculation",
    ),
    Module(
        "pawn-endings",
        "King and pawn endings",
        "foundation",
        (),
        "Practise opposition, key squares and pawn races from your own endgames; use local tablebases when available.",
        "endgame",
    ),
    Module(
        "rook-endings",
        "Rook endings",
        "intermediate",
        ("pawn-endings",),
        "Review rook activity, king activity and passed-pawn support. Replay a lost ending from both sides.",
        "endgame",
    ),
    Module(
        "defence",
        "Defensive technique",
        "advanced",
        ("candidate-moves",),
        "At a difficult position, identify the opponent's threat, then compare active defence with simplification.",
        "calculation",
    ),
    Module(
        "attack",
        "Attacking play",
        "advanced",
        ("tactics-advanced",),
        "Count attackers and defenders near the king. Calculate the defensive resources before committing a sacrifice.",
        "calculation",
    ),
    Module(
        "master-study",
        "Annotated master-game study",
        "advanced",
        ("opening-plans", "positional-strategy"),
        "Use a lawfully obtained annotated game. Predict critical moves and explain plans before reading annotations.",
        "calculation",
    ),
    Module(
        "clock-review",
        "Time management",
        "all levels",
        (),
        "Compare clock annotations at mistakes. Practise a consistent candidate-move routine in your usual time control.",
        "time management",
    ),
]


def curriculum(topic, rating=None, goal="general"):
    # Provider ratings are only a coarse study starting point, never a title or strength assessment.
    level = (
        "foundation" if rating is None or rating < 1200 else "intermediate" if rating < 1800 else "advanced"
    )
    allowed = {"foundation", "all levels"}
    if level != "foundation":
        allowed.add("intermediate")
    if level == "advanced":
        allowed.add("advanced")
    modules = [
        asdict(module) for module in CATALOGUE if module.topic == topic and module.difficulty in allowed
    ]
    return {
        "starting_level": level,
        "goal": goal,
        "modules": modules,
        "goal_activity": "Add slow practice games and a post-game review before your next event."
        if goal in {"tournament", "title-development"}
        else "Use a short daily practice session and review it weekly.",
        "level_note": "Approximate resource ordering from online rating; adjust with a coach and demonstrated exercise performance.",
    }
