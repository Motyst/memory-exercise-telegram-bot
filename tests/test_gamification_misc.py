"""Achievements, audio XP maths, sprint challenge — all pure or near-pure."""

from datetime import timedelta

import gamification.audio_xp  # noqa: F401 — registers the audio achievements
from gamification import ACHIEVEMENTS, AchievementContext, evaluate_achievements, get_achievement
from gamification.audio_xp import (
    DAILY_XP_CAP, LISTEN_XP, QUIZ_BASE_XP, QUIZ_PERFECT_BONUS, compute_raw_xp,
)
from bot import sprint
from database.models import utcnow


# ---- achievements ------------------------------------------------------------

def _ctx(**over):
    base = dict(score_pct=100, pair_count=10, difficulty="beginner", speed_mode=False,
                total_tests=1, streak=1, longest_streak=1)
    base.update(over)
    return AchievementContext(**base)


def test_first_perfect_test_unlocks_two():
    codes = [a.code for a in evaluate_achievements(_ctx(), set())]
    assert codes == ["first_test", "perfect_score"]


def test_already_unlocked_are_skipped_and_tiers_gate_on_count():
    new = evaluate_achievements(_ctx(pair_count=50), {"first_test", "perfect_score"})
    codes = {a.code for a in new}
    assert {"perfect_20", "perfect_50", "marathon_50"} <= codes
    assert "first_test" not in codes


def test_audio_achievements_ignore_word_memo_context():
    """Audio defs share the list; their isinstance guard must make them no-ops."""
    assert get_achievement("audio_first_listen") is not None
    codes = {a.code for a in evaluate_achievements(_ctx(total_tests=500), set())}
    assert not any(c.startswith("audio_") for c in codes)


def test_achievement_codes_are_unique():
    codes = [a.code for a in ACHIEVEMENTS]
    assert len(codes) == len(set(codes))


# ---- audio xp ---------------------------------------------------------------

def test_listen_xp_per_bucket_and_unknown_bucket_falls_back():
    assert compute_raw_xp("1min") == LISTEN_XP["1min"]
    assert compute_raw_xp("weird") == LISTEN_XP["3min"]


def test_quiz_xp_gates_and_bonus():
    assert compute_raw_xp("3min", quiz_score=1, quiz_max=3) == 0          # 33% < 50%
    assert compute_raw_xp("3min", quiz_score=3, quiz_max=3) == round(
        QUIZ_BASE_XP["3min"] * QUIZ_PERFECT_BONUS
    )
    assert compute_raw_xp("5min", quiz_score=2, quiz_max=3) < QUIZ_BASE_XP["5min"]
    assert DAILY_XP_CAP >= max(QUIZ_BASE_XP.values())


# ---- sprint -----------------------------------------------------------------

class RecordingRepo:
    def __init__(self):
        self.saved: dict = {}

    async def update_preferences(self, telegram_id, prefs):
        self.saved.update(prefs)


async def _run(prefs, **kw):
    repo = RecordingRepo()
    args = dict(count=10, difficulty="intermediate", speed_mode=False, score_pct=100)
    args.update(kw)
    line = await sprint.record_sprint_progress(repo, 1, prefs, **args)
    return line, repo.saved.get(sprint.PREF_KEY)


async def test_sprint_opens_on_qualifying_test():
    line, saved = await _run({})
    assert "started" in line
    assert saved["hits"] == 1 and saved["done"] is False


async def test_sprint_does_not_open_below_bar():
    line, saved = await _run({}, score_pct=88)
    assert saved is None and "need 90%" in line
    line, saved = await _run({}, count=5)
    assert saved is None and "need 10+" in line


async def test_sprint_requires_anchor_challenge_or_harder():
    now = utcnow()
    prefs = {sprint.PREF_KEY: {
        "date": now.date().isoformat(), "hits": 2, "anchor_cr": 12.5,
        "deadline": (now + timedelta(minutes=30)).isoformat(), "done": False,
    }}
    easier, saved = await _run(prefs, count=5, difficulty="beginner")
    assert saved is None and "easier" in easier
    harder, saved = await _run(prefs, count=20, difficulty="advanced")
    assert saved["hits"] == 3 and "3/5" in harder


async def test_sprint_completes_and_locks_for_the_day():
    now = utcnow()
    prefs = {sprint.PREF_KEY: {
        "date": now.date().isoformat(), "hits": sprint.SPRINT_GOAL - 1, "anchor_cr": 10,
        "deadline": (now + timedelta(minutes=10)).isoformat(), "done": False,
    }}
    line, saved = await _run(prefs)
    assert "COMPLETE" in line and saved["done"] is True
    again, saved2 = await _run({sprint.PREF_KEY: saved})
    assert "done today" in again and saved2 is None


async def test_sprint_expired_window_resets_and_reopens():
    now = utcnow()
    prefs = {sprint.PREF_KEY: {
        "date": now.date().isoformat(), "hits": 3, "anchor_cr": 10,
        "deadline": (now - timedelta(minutes=1)).isoformat(), "done": False,
    }}
    line, saved = await _run(prefs)
    assert "ran out" in line and saved["hits"] == 1
