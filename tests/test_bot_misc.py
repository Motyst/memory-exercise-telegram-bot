"""Handlers, rendering, analytics, reminders, version — the small pieces."""

from datetime import timedelta

from telegram import Update
from telegram.error import BadRequest, Forbidden, TimedOut

from bot import analytics
from bot.commands import _format_achievements, _format_leaderboard
from bot.handlers import error_handler
from bot.reminders import _within_window, claim_fresh_mind_bonus
from bot.version import format_changes, format_version, get_build_info
from database import UserRepository, get_session
from database.models import utcnow
from gamification import ACHIEVEMENTS
from tests.conftest import fresh_telegram_id
from tests.fakes import FakeBot, FakeContext


# ---- markdown safety ------------------------------------------------------

def test_leaderboard_escapes_user_names():
    text = _format_leaderboard([{
        "telegram_id": 1, "name": "an_na*[x]", "avg_pct": 90.0,
        "best_pct": 100.0, "tests": 3, "streak": 2,
    }], viewer_telegram_id=1)
    assert "an\\_na\\*\\[x]" in text
    assert "← you" in text and "🔥2" in text


def test_achievements_listing_counts_unlocked():
    first = ACHIEVEMENTS[0]
    text = _format_achievements({first.code: utcnow()})
    assert f"1/{len(ACHIEVEMENTS)}" in text
    assert f"*{first.name}* ✅" in text
    assert text.count("🔒") == len(ACHIEVEMENTS) - 1


# ---- error handler ----------------------------------------------------------

class _Upd(Update):
    def __init__(self, uid):  # skip PTB's constructor
        self._uid = uid

    @property
    def effective_user(self):
        return type("U", (), {"id": self._uid})()

    @property
    def effective_chat(self):
        return type("C", (), {"id": self._uid})()


async def test_error_handler_swallows_expected_noise():
    ctx = FakeContext(1)
    for err in (Forbidden("blocked"), BadRequest("Message is not modified"), TimedOut()):
        ctx.error = err
        await error_handler(_Upd(1), ctx)
    assert ctx.bot.sent == []


async def test_error_handler_tells_user_once_and_tolerates_non_update():
    ctx = FakeContext(1)
    try:
        raise RuntimeError("boom")
    except RuntimeError as e:
        ctx.error = e
        await error_handler(_Upd(1), ctx)
    assert len(ctx.bot.sent) == 1 and "/start" in ctx.bot.sent[0]
    ctx.error = RuntimeError("job crash")
    await error_handler("not-an-update", ctx)
    assert len(ctx.bot.sent) == 1


# ---- analytics ---------------------------------------------------------------

def test_round_duration_caps_and_missing_stamp():
    assert analytics.round_duration_s({}) is None
    st = {}
    analytics.mark_round_start(st)
    assert analytics.round_duration_s(st) == 0
    st[analytics.ROUND_START_KEY] -= 10_000
    assert analytics.round_duration_s(st) is None                 # over MAX_ROUND_SECONDS
    st2 = {}
    analytics.mark_round_start(st2)
    st2[analytics.ROUND_START_KEY] -= 500
    assert analytics.round_duration_s(st2, cap=300) is None        # over caller cap
    assert analytics.round_duration_s(st2, cap=600) == 500


async def test_touch_last_active_is_throttled_and_persists():
    tid = fresh_telegram_id()
    async with get_session() as s:
        u, _ = await UserRepository(s).get_or_create(telegram_id=tid)
        u.last_active_at = utcnow() - timedelta(days=2)
    before = len(analytics._pending)
    analytics.touch_last_active(tid)
    analytics.touch_last_active(tid)
    assert len(analytics._pending) == before + 1
    await analytics._pending_snapshot_wait()
    async with get_session() as s:
        u = await UserRepository(s).get_by_telegram_id(tid)
    assert utcnow() - u.last_active_at < timedelta(minutes=1)


async def test_retention_job_runs_without_error():
    ctx = FakeContext(1)
    await analytics.purge_old_events(ctx)   # empty or not — must not raise


# ---- reminders ---------------------------------------------------------------

def test_fresh_mind_window():
    assert not _within_window(None)
    assert not _within_window("garbage")
    assert _within_window((utcnow() - timedelta(minutes=5)).isoformat())
    assert not _within_window((utcnow() - timedelta(minutes=30)).isoformat())


class _PrefRepo:
    def __init__(self):
        self.writes = []

    async def update_preferences(self, tid, prefs):
        self.writes.append(prefs)


class _User:
    telegram_id = 1
    preferences = {"reminder": {"enabled": True}}


async def test_fresh_mind_bonus_pays_once_per_day():
    repo = _PrefRepo()
    bonus = await claim_fresh_mind_bonus(repo, _User(), 100)
    assert bonus == 25
    today = utcnow().date().isoformat()
    assert repo.writes[-1]["reminder"]["bonus_date"] == today
    used = _User()
    used.preferences = {"reminder": {"bonus_date": today}}
    assert await claim_fresh_mind_bonus(repo, used, 100) == 0


# ---- version ---------------------------------------------------------------

def test_version_reads_git_and_renders():
    b = get_build_info()
    assert b.short != "unknown" and b.history
    assert b.short in format_version() and "Up since" in format_version()
    assert format_changes(3).count("\n• ") == 3
