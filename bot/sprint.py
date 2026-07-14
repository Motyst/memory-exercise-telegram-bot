"""
Daily sprint challenge — cosmetic mini-goal shown on word-memo test results.

Goal: SPRINT_GOAL fresh tests at >=SPRINT_PASS_PCT inside one
SPRINT_WINDOW_MINUTES window. Rules:

- Only fresh scored word-memo tests count (round_mode "test" — retry /
  reverse / placement rounds and audio sessions never touch it).
- The first qualifying test needs >= SPRINT_MIN_WORDS words and opens the
  run; its challenge rating (count x difficulty x speed — same formula as
  XP) becomes the anchor. Later tests only count at anchor CR or higher, so
  a run started on 10 intermediate can't be finished on 5-word beginner.
- A test below the bar simply doesn't count — the run keeps going.
- Window expires -> run silently resets; the expiring test can immediately
  open a new run. Completing the sprint marks it done until midnight UTC.
- Purely cosmetic: no XP, no achievements, nothing excluded from stats.
  (Future hook: award an XP multiplier on the completing 5th test — apply it
  where record_sprint_progress returns the completion line.)

Progress lives in user preferences under PREF_KEY:
    {"date": "2026-07-14", "hits": 2, "anchor_cr": 12.5,
     "deadline": "2026-07-14T15:32:00+00:00", "done": false}

Removal: delete this module, the sprint block + import in
bot/quiz_engine.py::_show_test_results, SPRINT_ENABLED_KEY in
bot/features.py, and the /admin sprint subcommand. Stale "sprint" keys left
in user preferences are harmless.
"""

import logging
from datetime import datetime, timedelta

from gamification.xp import challenge_rating
from database.models import utcnow
from .features import is_flag_enabled, SPRINT_ENABLED_KEY

logger = logging.getLogger(__name__)

SPRINT_GOAL = 5
SPRINT_WINDOW_MINUTES = 60
SPRINT_MIN_WORDS = 10       # floor for the OPENING test (stops low anchors)
SPRINT_PASS_PCT = 90.0
PREF_KEY = "sprint"


def _circles(hits: int) -> str:
    return "🟢" * hits + "⚪" * (SPRINT_GOAL - hits)


def _minutes_left(deadline: datetime, now: datetime) -> int:
    return max(int((deadline - now).total_seconds() // 60), 0)


async def record_sprint_progress(
    user_repo, telegram_id: int, prefs: dict | None,
    *, count: int, difficulty: str, speed_mode: bool, score_pct: float,
) -> str | None:
    """Evaluate one fresh test against today's sprint; return the results line.

    Persists updated progress via user_repo (caller's open DB session).
    """
    if not is_flag_enabled(SPRINT_ENABLED_KEY):
        return None

    now = utcnow()
    today = now.date().isoformat()
    s = (prefs or {}).get(PREF_KEY) or {}
    if s.get("date") != today:
        s = {}

    if s.get("done"):
        return f"🏁 {_circles(SPRINT_GOAL)} Sprint done today — next at midnight UTC"

    expired_note = None
    if s.get("hits"):
        deadline = datetime.fromisoformat(s["deadline"])
        if now > deadline:
            expired_note = "⏱ _Sprint window ran out — run over._"
            s = {}

    passed = score_pct >= SPRINT_PASS_PCT
    cr = challenge_rating(count, difficulty, speed_mode)

    if not s.get("hits"):
        # No active run — a qualifying test opens one.
        if passed and count >= SPRINT_MIN_WORDS:
            s = {
                "date": today, "hits": 1, "anchor_cr": cr,
                "deadline": (now + timedelta(minutes=SPRINT_WINDOW_MINUTES)).isoformat(),
                "done": False,
            }
            await user_repo.update_preferences(telegram_id, {PREF_KEY: s})
            line = (
                f"🏁 {_circles(1)} *Daily sprint started!* "
                f"{SPRINT_GOAL - 1} more 90%+ tests in {SPRINT_WINDOW_MINUTES} min "
                "(this level or harder)"
            )
        else:
            line = (
                f"🏁 {_circles(0)} Daily sprint: {SPRINT_GOAL}× 90%+ tests "
                f"({SPRINT_MIN_WORDS}+ words) within {SPRINT_WINDOW_MINUTES} min"
            )
        return f"{expired_note}\n{line}" if expired_note else line

    # Active run.
    deadline = datetime.fromisoformat(s["deadline"])
    if passed and cr >= s["anchor_cr"] - 1e-9:
        hits = s["hits"] + 1
        if hits >= SPRINT_GOAL:
            await user_repo.update_preferences(
                telegram_id, {PREF_KEY: {"date": today, "hits": SPRINT_GOAL, "done": True}}
            )
            return (
                f"🏆 {_circles(SPRINT_GOAL)} *DAILY SPRINT COMPLETE!* "
                f"{SPRINT_GOAL}× 90%+ inside {SPRINT_WINDOW_MINUTES} minutes — sharp mind."
            )
        await user_repo.update_preferences(telegram_id, {PREF_KEY: {**s, "hits": hits}})
        return (
            f"🏁 {_circles(hits)} Sprint {hits}/{SPRINT_GOAL} — "
            f"{_minutes_left(deadline, now)} min left"
        )

    # Didn't count — run untouched.
    reason = "below 90%" if not passed else "easier than your opening test"
    return (
        f"🏁 {_circles(s['hits'])} Sprint {s['hits']}/{SPRINT_GOAL} — "
        f"{_minutes_left(deadline, now)} min left · _this one didn't count ({reason})_"
    )
