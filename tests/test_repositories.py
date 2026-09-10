"""SQL-side rules against a real (temporary) SQLite file: which rounds count
as scored tests, personal bests, leaderboard gating, admin exclusion."""

from datetime import timedelta

from database import (
    ActivityEvent, ExerciseSessionRepository, ExerciseType, UserRepository,
    ActivityEventRepository, get_session,
)
from database.models import utcnow
from tests.conftest import ADMIN_ID, fresh_telegram_id


async def _user(session, telegram_id=None, opt_in=False):
    tid = telegram_id or fresh_telegram_id()
    user, _ = await UserRepository(session).get_or_create(telegram_id=tid, first_name=f"u{tid}")
    if opt_in:
        user.leaderboard_opt_in = True
    return user


async def _round(session, user, mode, score, max_score=10, count=10,
                 difficulty="beginner", fmt="pairs", exercise=ExerciseType.WORD_MEMORIZATION):
    return await ExerciseSessionRepository(session).create(
        user_id=user.id, exercise_type=exercise, difficulty=difficulty,
        parameters={"count": count, "mode": mode, "format": fmt},
        score=score, max_score=max_score, completed=True,
    )


async def test_scored_test_filter_excludes_practice_rounds():
    async with get_session() as s:
        u = await _user(s)
        for mode, score in [("test", 8), ("reverse", 9), ("retry", 10),
                            ("placement", 10), ("reverse_extra", 10), ("audio_quiz", 3)]:
            await _round(s, u, mode, score)
        repo = ExerciseSessionRepository(s)
        assert await repo.count_completed_tests(u.id) == 2      # test + first reverse
        stats = await repo.get_user_stats(u.id)
        assert stats["tests_total"] == 2
        history = await repo.get_recent_test_history(u.id)
        assert {h["mode"] for h in history} == {"test", "reverse"}


async def test_legacy_rows_without_mode_count_as_tests():
    async with get_session() as s:
        u = await _user(s)
        await ExerciseSessionRepository(s).create(
            user_id=u.id, exercise_type=ExerciseType.WORD_MEMORIZATION,
            difficulty="beginner", parameters={"count": 5},
            score=5, max_score=5, completed=True,
        )
        assert await ExerciseSessionRepository(s).count_completed_tests(u.id) == 1


async def test_personal_best_is_per_difficulty_count_and_format():
    async with get_session() as s:
        u = await _user(s)
        await _round(s, u, "test", 7)                       # beginner/10/pairs 70%
        await _round(s, u, "test", 9, fmt="list")           # beginner/10/list 90%
        await _round(s, u, "retry", 10)                     # never a PB
        repo = ExerciseSessionRepository(s)
        assert await repo.get_personal_best(u.id, "beginner", 10) == 70
        assert await repo.get_personal_best(u.id, "beginner", 10, fmt="list") == 90
        assert await repo.get_personal_best(u.id, "beginner", 20) is None
        assert await repo.get_personal_best(u.id, "advanced", 10) is None


async def test_mastered_is_biggest_count_at_90_plus():
    async with get_session() as s:
        u = await _user(s)
        await _round(s, u, "test", 20, max_score=20, count=20)   # 100% on 20
        await _round(s, u, "test", 30, max_score=50, count=50)   # 60% on 50
        stats = await ExerciseSessionRepository(s).get_user_stats(u.id)
        assert stats["by_difficulty"]["beginner"]["mastered"] == 20
        assert stats["latest_pairs"] == 50


async def test_leaderboard_min_tests_opt_in_and_admin_exclusion():
    async with get_session() as s:
        ranked = await _user(s, opt_in=True)
        too_few = await _user(s, opt_in=True)
        hidden = await _user(s, opt_in=False)
        admin = await _user(s, telegram_id=ADMIN_ID, opt_in=True)
        for u in (ranked, hidden, admin):
            for _ in range(3):
                await _round(s, u, "test", 10)
        await _round(s, too_few, "test", 10)
        board = await ExerciseSessionRepository(s).get_leaderboard(
            min_tests=3, exclude_telegram_ids={ADMIN_ID},
        )
        ids = [e["telegram_id"] for e in board]
        assert ranked.telegram_id in ids
        assert too_few.telegram_id not in ids
        assert hidden.telegram_id not in ids
        assert ADMIN_ID not in ids


async def test_rank_for_user_counts_opted_out_viewer_into_board():
    async with get_session() as s:
        leader = await _user(s, opt_in=True)
        viewer = await _user(s, opt_in=False)
        for _ in range(3):
            await _round(s, leader, "test", 10)
            await _round(s, viewer, "test", 5)
        repo = ExerciseSessionRepository(s)
        info = await repo.get_rank_for_user(viewer.telegram_id, min_tests=3)
        assert info["opted_in"] is False
        assert info["rank"] >= 2 and info["total"] >= 2
        assert info["avg_pct"] == 50
        assert await repo.get_rank_for_user(ADMIN_ID, exclude_telegram_ids={ADMIN_ID}) is None


async def test_rank_for_user_below_min_tests_reports_progress():
    async with get_session() as s:
        u = await _user(s)
        await _round(s, u, "test", 10)
        info = await ExerciseSessionRepository(s).get_rank_for_user(u.telegram_id, min_tests=3)
        assert info["rank"] is None and info["tests"] == 1


async def test_streak_increments_once_per_day_and_resets_after_gap():
    async with get_session() as s:
        u = await _user(s)
        repo = UserRepository(s)
        first = await repo.update_streak(u.telegram_id)
        assert first == {"streak": 1, "longest": 1, "is_first_today": True}
        again = await repo.update_streak(u.telegram_id)
        assert again["is_first_today"] is False and again["streak"] == 1
        u.last_trained_date = utcnow().date() - timedelta(days=1)
        assert (await repo.update_streak(u.telegram_id))["streak"] == 2
        u.last_trained_date = utcnow().date() - timedelta(days=3)
        broken = await repo.update_streak(u.telegram_id)
        assert broken["streak"] == 1 and broken["longest"] == 2


async def test_preferences_merge_not_replace():
    async with get_session() as s:
        u = await _user(s)
        repo = UserRepository(s)
        await repo.update_preferences(u.telegram_id, {"a": 1})
        user = await repo.update_preferences(u.telegram_id, {"b": 2})
        assert user.preferences == {"a": 1, "b": 2}


async def test_touch_last_active_updates_without_row_load():
    async with get_session() as s:
        u = await _user(s)
        u.last_active_at = utcnow() - timedelta(days=5)
    async with get_session() as s:
        await UserRepository(s).touch_last_active(u.telegram_id)
    async with get_session() as s:
        user = await UserRepository(s).get_by_telegram_id(u.telegram_id)
        assert utcnow() - user.last_active_at < timedelta(minutes=1)


async def test_activity_purge_removes_only_old_rows():
    tid = fresh_telegram_id()
    async with get_session() as s:
        s.add(ActivityEvent(telegram_id=tid, kind="command", detail="/old",
                            ts=utcnow() - timedelta(days=100)))
        await ActivityEventRepository(s).log(tid, "command", "/new")
    async with get_session() as s:
        purged = await ActivityEventRepository(s).purge_older_than(90)
        assert purged >= 1
    async with get_session() as s:
        assert await ActivityEventRepository(s).get_active_days(
            tid, utcnow() - timedelta(days=1)
        ) == 1
