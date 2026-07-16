"""
Audio Visualization XP + achievements — self-contained, removable.

Feeds a separate "visualization" skill bar (registered into SKILLS at import
time, so /level shows it automatically). Audio is a much easier exercise than
word memorization, so the XP economy is deliberately stricter — layered
anti-farm design:

1. Separate skill bar — audio XP can never inflate the mnemonics level.
2. First-listen-only — a story already in the user's `audio_heard` anti-repeat
   list earns 0 XP (kills replay farming; the library is finite).
3. Passive listens pay a small fixed amount per length bucket (passive work is
   unverifiable, so it must stay cheap).
4. The detail quiz pays real XP scaled by accuracy² with a 50% floor, like
   word-memo tests (the quiz already has its own anti-cheat: audio deleted
   before questions, options shuffled).
5. Hard daily cap (DAILY_XP_CAP) as a backstop, tracked per UTC day in
   `preferences["audio_xp_day"]`.

No hard-streak / challenge-rating mechanics — those exist to push word-memo
users toward harder tests; audio has no comparable difficulty axis yet.

Feature flag: `audio_xp_enabled` (/admin audioxp on|off, default ON). The
caller passes the combined flag state in (`xp_enabled`) — this module must not
import bot.features (circular import via bot/__init__).

Removal (delete this file, then):
- bot/audio_viz.py: `process_audio_completion` import + the two call sites
  (_record_distractions, _show_quiz_results) + the xp_lines message additions
- bot/features.py: AUDIO_XP_ENABLED_KEY + its _flags default
- bot/admin.py: "audioxp" subcommand + overview/help lines
- database/repositories.py: get_audio_achievement_stats()
Leftover user data is harmless: "visualization" rows in user_skills, unlocked
audio_* achievements (no longer displayed once unregistered), and the
`audio_xp_day` preference.

Rebalancing = edit the constants below; docs in ADMIN_GUIDE.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from database import (
    get_session, UserRepository, ExerciseSessionRepository,
    AchievementRepository, UserSkillRepository,
)
from .achievements import AchievementDef, register_achievements
from .xp import SKILLS, SkillDef, level_from_xp

logger = logging.getLogger(__name__)

SKILL_CODE = "visualization"

# Registered at import time (bot/audio_viz.py imports this module at startup),
# so the bar appears in /level without touching xp.py.
SKILLS[SKILL_CODE] = SkillDef(
    SKILL_CODE, "👁", "Visualization",
    "Grows with audio story sessions",
)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Fixed XP for a first-time passive listen, per length bucket.
LISTEN_XP = {"1min": 5, "3min": 12, "5min": 20}

# Base XP for a first-time detail quiz (includes the listen credit),
# scaled by accuracy² — mirrors word-memo's accuracy exponent.
QUIZ_BASE_XP = {"1min": 15, "3min": 30, "5min": 45}
QUIZ_MIN_ACCURACY = 0.5      # below this the quiz pays nothing (anti-farm)
QUIZ_ACCURACY_EXPONENT = 2.0
QUIZ_PERFECT_BONUS = 1.2

# Hard backstop: max audio XP per UTC day (~2-3 honest sessions).
DAILY_XP_CAP = 80
XP_DAY_PREF_KEY = "audio_xp_day"  # {"date": "YYYY-MM-DD", "xp": int}

# ---------------------------------------------------------------------------
# Audio achievements
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AudioAchievementContext:
    """Lifetime audio counters (SQL-aggregated), incl. the session just saved."""
    total_sessions: int    # completed audio sessions, listens + quizzes
    distinct_stories: int  # unique story ids ever completed
    perfect_quizzes: int   # detail quizzes at 100%


def _audio_check(check):
    """Guard: these defs sit in the shared ACHIEVEMENTS list for display, so
    word-memo's evaluate_achievements also calls them — with its own context.
    The isinstance gate makes them a no-op there."""
    return lambda c: isinstance(c, AudioAchievementContext) and check(c)


AUDIO_ACHIEVEMENTS: list[AchievementDef] = [
    AchievementDef(
        "audio_first_listen", "🎧", "First Listen",
        "Complete your first audio story",
        _audio_check(lambda c: c.total_sessions >= 1),
    ),
    AchievementDef(
        "audio_collector_10", "📚", "Story Collector",
        "Complete 10 different stories",
        _audio_check(lambda c: c.distinct_stories >= 10),
    ),
    AchievementDef(
        "audio_perfect_3", "🎬", "Perfect Recall",
        "Score 100% on 3 detail tests",
        _audio_check(lambda c: c.perfect_quizzes >= 3),
    ),
]

register_achievements(AUDIO_ACHIEVEMENTS)


# ---------------------------------------------------------------------------
# Award pipeline
# ---------------------------------------------------------------------------

async def process_audio_completion(
    telegram_id: int, bucket: str, *,
    first_time: bool, xp_enabled: bool,
    quiz_score: int | None = None, quiz_max: int | None = None,
) -> list[str]:
    """XP + achievement check after an audio session was saved.

    Returns Markdown lines to append to the completion/results message.
    Achievements are evaluated even when XP is off (same policy as word-memo).
    Never raises — a gamification hiccup must not break the exercise flow.
    """
    lines: list[str] = []
    try:
        async with get_session() as session:
            db_user = await UserRepository(session).get_by_telegram_id(telegram_id)
            if not db_user:
                return []
            if xp_enabled:
                lines += await _award_xp(
                    session, db_user, bucket, first_time, quiz_score, quiz_max,
                )
            lines += await _check_achievements(session, db_user)
    except Exception as e:
        logger.error(f"Audio XP/achievement processing failed for {telegram_id}: {e}")
    return lines


def compute_raw_xp(
    bucket: str, quiz_score: int | None = None, quiz_max: int | None = None,
) -> int:
    """Uncapped XP for one session. Pure function (unknown bucket → mid tier)."""
    if quiz_max:
        accuracy = (quiz_score or 0) / quiz_max
        if accuracy < QUIZ_MIN_ACCURACY:
            return 0
        xp = (
            QUIZ_BASE_XP.get(bucket, QUIZ_BASE_XP["3min"])
            * accuracy ** QUIZ_ACCURACY_EXPONENT
        )
        if accuracy >= 1.0:
            xp *= QUIZ_PERFECT_BONUS
        return round(xp)
    return LISTEN_XP.get(bucket, LISTEN_XP["3min"])


async def _award_xp(
    session, db_user, bucket: str, first_time: bool,
    quiz_score: int | None, quiz_max: int | None,
) -> list[str]:
    raw = compute_raw_xp(bucket, quiz_score, quiz_max)
    if raw <= 0:
        if quiz_max:
            return ["⭐ No XP — detail test below 50%."]
        return []
    if not first_time:
        return ["🔁 Replayed story — XP is for first-time listens only."]

    today = datetime.now(timezone.utc).date().isoformat()
    tracker = (db_user.preferences or {}).get(XP_DAY_PREF_KEY) or {}
    used = tracker.get("xp", 0) if tracker.get("date") == today else 0
    award = min(raw, DAILY_XP_CAP - used)
    if award <= 0:
        return ["🎧 Daily audio XP cap reached — earns again tomorrow."]

    skill_repo = UserSkillRepository(session)
    row = await skill_repo.get_or_create(db_user.id, SKILL_CODE)
    old_level = row.level
    new_level, xp_into, xp_need = level_from_xp(row.xp + award)
    await skill_repo.add_xp(db_user.id, SKILL_CODE, award, new_level, row.hard_streak)
    await UserRepository(session).update_preferences(
        db_user.telegram_id, {XP_DAY_PREF_KEY: {"date": today, "xp": used + award}}
    )

    skill = SKILLS[SKILL_CODE]
    lines = [
        f"⭐ *+{award} XP* {skill.emoji} {skill.name} — "
        f"Level {new_level} ({xp_into}/{xp_need})"
    ]
    if award < raw:
        lines.append("🎧 Daily audio XP cap reached — earns again tomorrow.")
    if new_level > old_level:
        lines.append(f"🎉 *LEVEL UP!* {skill.name} is now *Level {new_level}*")
    return lines


async def _check_achievements(session, db_user) -> list[str]:
    stats = await ExerciseSessionRepository(session).get_audio_achievement_stats(
        db_user.id
    )
    ctx = AudioAchievementContext(**stats)
    achievement_repo = AchievementRepository(session)
    unlocked = await achievement_repo.get_unlocked_codes(db_user.id)
    new = [
        a for a in AUDIO_ACHIEVEMENTS
        if a.code not in unlocked and a.check(ctx)
    ]
    if not new:
        return []
    await achievement_repo.unlock(db_user.id, [a.code for a in new])
    lines = ["🏅 *Achievement unlocked!*"]
    lines += [f"{a.emoji} *{a.name}* — {a.description}" for a in new]
    return lines
