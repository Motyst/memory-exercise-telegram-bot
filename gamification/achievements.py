"""
Achievement definitions and evaluation.

Definitions live in code (no DB migration to add one) — only unlocks are
stored, in the user_achievements table. To add an achievement, append an
AchievementDef here; it shows up in /achievements and is checked after
every completed test automatically.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AchievementContext:
    """Snapshot of the just-completed test + user progress, passed to checks."""
    score_pct: float          # final score of this test, 0-100
    pair_count: int           # pairs in this test
    difficulty: str           # "beginner" | "intermediate" | "advanced"
    speed_mode: bool          # study phase ran at half time
    total_tests: int          # completed tests including this one
    streak: int               # current daily streak
    longest_streak: int


@dataclass(frozen=True)
class AchievementDef:
    code: str
    emoji: str
    name: str
    description: str
    check: Callable[[AchievementContext], bool]


ACHIEVEMENTS: list[AchievementDef] = [
    AchievementDef(
        "first_test", "🎓", "First Steps",
        "Complete your first test",
        lambda c: c.total_tests >= 1,
    ),
    # Tiered achievements: same feat at growing pair counts (I/II/III).
    # 90%+ on 5 pairs is much easier than on 30 — tiers keep them meaningful.
    AchievementDef(
        "perfect_score", "💯", "Flawless I",
        "Score 100% on any test",
        lambda c: c.score_pct >= 100,
    ),
    AchievementDef(
        "perfect_20", "🎯", "Flawless II",
        "Score 100% on a test with 20+ pairs",
        lambda c: c.score_pct >= 100 and c.pair_count >= 20,
    ),
    AchievementDef(
        "perfect_50", "💎", "Flawless III",
        "Score 100% on a test with 50+ pairs",
        lambda c: c.score_pct >= 100 and c.pair_count >= 50,
    ),
    AchievementDef(
        "marathon_50", "🏃", "Marathon Mind",
        "Complete a 50+ pair test",
        lambda c: c.pair_count >= 50,
    ),
    AchievementDef(
        "century", "💪", "Centurion",
        "Complete a 100 pair test",
        lambda c: c.pair_count >= 100,
    ),
    AchievementDef(
        "speedster", "⚡", "Speedster I",
        "Score 90%+ in speed mode (10+ pairs)",
        lambda c: c.speed_mode and c.score_pct >= 90 and c.pair_count >= 10,
    ),
    AchievementDef(
        "speedster_2", "⚡", "Speedster II",
        "Score 90%+ in speed mode (30+ pairs)",
        lambda c: c.speed_mode and c.score_pct >= 90 and c.pair_count >= 30,
    ),
    AchievementDef(
        "speedster_3", "⚡", "Speedster III",
        "Score 90%+ in speed mode (50+ pairs)",
        lambda c: c.speed_mode and c.score_pct >= 90 and c.pair_count >= 50,
    ),
    AchievementDef(
        "advanced_ace", "🧠", "Advanced Ace I",
        "Score 90%+ on Advanced difficulty (10+ pairs)",
        lambda c: c.difficulty == "advanced" and c.score_pct >= 90 and c.pair_count >= 10,
    ),
    AchievementDef(
        "advanced_ace_2", "🧠", "Advanced Ace II",
        "Score 90%+ on Advanced difficulty (30+ pairs)",
        lambda c: c.difficulty == "advanced" and c.score_pct >= 90 and c.pair_count >= 30,
    ),
    AchievementDef(
        "advanced_ace_3", "🧠", "Advanced Ace III",
        "Score 90%+ on Advanced difficulty (50+ pairs)",
        lambda c: c.difficulty == "advanced" and c.score_pct >= 90 and c.pair_count >= 50,
    ),
    AchievementDef(
        "streak_3", "🔥", "Warming Up",
        "Train 3 days in a row",
        lambda c: c.streak >= 3,
    ),
    AchievementDef(
        "streak_7", "📅", "Week Strong",
        "Train 7 days in a row",
        lambda c: c.streak >= 7,
    ),
    AchievementDef(
        "streak_30", "🏆", "Iron Discipline",
        "Train 30 days in a row",
        lambda c: c.streak >= 30,
    ),
    AchievementDef(
        "tests_10", "📈", "Getting Serious",
        "Complete 10 tests",
        lambda c: c.total_tests >= 10,
    ),
    AchievementDef(
        "tests_50", "🎖", "Dedicated Trainee",
        "Complete 50 tests",
        lambda c: c.total_tests >= 50,
    ),
    AchievementDef(
        "tests_100", "👑", "Memory Master",
        "Complete 100 tests",
        lambda c: c.total_tests >= 100,
    ),
]

_BY_CODE = {a.code: a for a in ACHIEVEMENTS}


def get_achievement(code: str) -> AchievementDef | None:
    return _BY_CODE.get(code)


def evaluate_achievements(
    ctx: AchievementContext, already_unlocked: set[str],
) -> list[AchievementDef]:
    """Return newly earned achievements, in definition order."""
    return [
        a for a in ACHIEVEMENTS
        if a.code not in already_unlocked and a.check(ctx)
    ]
