"""
Repository pattern for database operations.
Provides clean interface for CRUD operations on models.

Stats/leaderboard queries aggregate in SQL (not Python loops) so they stay
fast as the session table grows.
"""

from datetime import datetime, timedelta, date
from typing import Optional
from sqlalchemy import select, func, and_, or_, case
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    User, ExerciseSession, ExerciseType, SubscriptionTier, UserAchievement,
    UserSkill, BotSetting, utcnow,
)

# Score percentage expression reused across queries.
_PCT = ExerciseSession.score * 100.0 / ExerciseSession.max_score

# Quiz round type stored in parameters JSON: "test" | "reverse" | "retry".
_ROUND_MODE = ExerciseSession.parameters["mode"].as_string()

# Any session that produced a score (includes retry-mistakes rounds).
_HAS_SCORE = and_(
    ExerciseSession.max_score.isnot(None),
    ExerciseSession.max_score > 0,
)

# Filter for real tests: scored, and not a retry-mistakes round. Retries are
# practice on a subset of pairs — counting them would inflate test counts and
# average scores (stats, leaderboard, personal bests). Reverse quizzes DO
# count — they test the full set, just column-flipped.
_IS_SCORED_TEST = and_(
    _HAS_SCORE,
    or_(_ROUND_MODE.is_(None), _ROUND_MODE != "retry"),
)


class UserRepository:
    """Repository for User operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self, telegram_id: int, username=None, first_name=None,
        last_name=None, language_code: str = "en",
    ) -> User:
        user = User(
            telegram_id=telegram_id, username=username,
            first_name=first_name, last_name=last_name,
            language_code=language_code,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_or_create(
        self, telegram_id: int, username=None, first_name=None,
        last_name=None, language_code: str = "en",
    ) -> tuple[User, bool]:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.last_active_at = utcnow()
            return user, False
        user = await self.create(
            telegram_id=telegram_id, username=username,
            first_name=first_name, last_name=last_name,
            language_code=language_code,
        )
        return user, True

    async def update_streak(self, telegram_id: int) -> dict:
        """
        Update daily training streak for user.
        Returns {"streak": int, "longest": int, "is_first_today": bool}.
        Call once per completed test session.
        """
        user = await self.get_by_telegram_id(telegram_id)
        if not user:
            return {"streak": 0, "longest": 0, "is_first_today": False}

        today = date.today()
        last = user.last_trained_date

        if last == today:
            # Already counted today
            return {
                "streak": user.current_streak or 0,
                "longest": user.longest_streak or 0,
                "is_first_today": False,
            }

        yesterday = today - timedelta(days=1)
        if last == yesterday:
            user.current_streak = (user.current_streak or 0) + 1
        else:
            user.current_streak = 1

        user.last_trained_date = today
        if user.current_streak > (user.longest_streak or 0):
            user.longest_streak = user.current_streak

        await self.session.flush()
        return {
            "streak": user.current_streak,
            "longest": user.longest_streak,
            "is_first_today": True,
        }

    async def update_preferences(self, telegram_id: int, preferences: dict) -> Optional[User]:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            # Assign a NEW dict — SQLAlchemy JSON change detection compares
            # object identity, so mutating the loaded dict in place is
            # silently ignored at flush time.
            user.preferences = {**(user.preferences or {}), **preferences}
            await self.session.flush()
        return user

    async def set_leaderboard_opt_in(self, telegram_id: int, opt_in: bool) -> Optional[User]:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.leaderboard_opt_in = opt_in
            await self.session.flush()
        return user

    async def set_subscription(
        self, telegram_id: int, tier: SubscriptionTier,
        expires_at: Optional[datetime] = None,
    ) -> Optional[User]:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.subscription_tier = tier
            user.subscription_expires_at = expires_at
            await self.session.flush()
        return user


class ExerciseSessionRepository:
    """Repository for ExerciseSession operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, user_id: int, exercise_type: ExerciseType,
        difficulty: str, parameters: dict,
        score: Optional[int] = None, max_score: Optional[int] = None,
        completed: bool = False,
    ) -> ExerciseSession:
        exercise_session = ExerciseSession(
            user_id=user_id, exercise_type=exercise_type,
            difficulty=difficulty, parameters=parameters,
            score=score, max_score=max_score, completed=completed,
            completed_at=utcnow() if completed else None,
        )
        self.session.add(exercise_session)
        await self.session.flush()
        return exercise_session

    async def get_user_sessions(
        self, user_id: int, exercise_type: Optional[ExerciseType] = None,
        limit: int = 10,
    ) -> list[ExerciseSession]:
        query = select(ExerciseSession).where(ExerciseSession.user_id == user_id)
        if exercise_type:
            query = query.where(ExerciseSession.exercise_type == exercise_type)
        query = query.order_by(ExerciseSession.started_at.desc()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_user_stats(self, user_id: int) -> dict:
        """
        Test statistics broken down per difficulty — computed in SQL.
        Retry-mistakes rounds are excluded (see _IS_SCORED_TEST).

        "mastered" = biggest pair count with a 90%+ score at that difficulty
        (a raw best-% would hide the huge gap between acing 5 and 50 pairs).

        Returns {
            "tests_total": int,
            "by_difficulty": {difficulty: {"tests", "mastered"}},  # mastered: int | None
            "latest_pct": float, "latest_pairs": int | None,
        }
        """
        stats = {
            "tests_total": 0, "by_difficulty": {},
            "latest_pct": 0.0, "latest_pairs": None,
        }
        pair_count = ExerciseSession.parameters["count"].as_integer()

        rows = await self.session.execute(
            select(
                ExerciseSession.difficulty,
                func.count(),
                func.max(case((_PCT >= 90, pair_count))),
            )
            .where(ExerciseSession.user_id == user_id, _IS_SCORED_TEST)
            .group_by(ExerciseSession.difficulty)
        )
        for difficulty, count, mastered in rows:
            stats["by_difficulty"][difficulty] = {
                "tests": count,
                "mastered": mastered,
            }
            stats["tests_total"] += count

        if stats["tests_total"]:
            latest = (await self.session.execute(
                select(_PCT, pair_count)
                .where(ExerciseSession.user_id == user_id, _IS_SCORED_TEST)
                .order_by(ExerciseSession.started_at.desc())
                .limit(1)
            )).one_or_none()
            if latest:
                stats["latest_pct"] = latest[0] or 0.0
                stats["latest_pairs"] = latest[1]

        return stats

    async def count_completed_tests(self, user_id: int) -> int:
        """Total scored test sessions for a user (used by achievements)."""
        return (await self.session.execute(
            select(func.count())
            .where(ExerciseSession.user_id == user_id, _IS_SCORED_TEST)
        )).scalar_one()

    async def get_personal_best(
        self, user_id: int, difficulty: str, count: int,
    ) -> float | None:
        """
        Return the best score percentage for a given difficulty + pair count,
        or None if no previous test exists for that combo.
        """
        return (await self.session.execute(
            select(func.max(_PCT)).where(
                ExerciseSession.user_id == user_id,
                ExerciseSession.difficulty == difficulty,
                ExerciseSession.parameters["count"].as_integer() == count,
                _IS_SCORED_TEST,
            )
        )).scalar_one_or_none()

    async def get_recent_test_history(
        self, user_id: int, limit: int = 10,
    ) -> list[dict]:
        """
        Return the last *limit* test-mode sessions as a list of dicts:
        [{date, difficulty, count, score, max_score, pct}, ...]
        Newest first.
        """
        result = await self.session.execute(
            select(ExerciseSession)
            .where(ExerciseSession.user_id == user_id, _IS_SCORED_TEST)
            .order_by(ExerciseSession.started_at.desc())
            .limit(limit)
        )
        history = []
        for sess in result.scalars():
            params = sess.parameters or {}
            history.append({
                "date": sess.started_at,
                "difficulty": sess.difficulty,
                "count": params.get("count", "?"),
                "mode": params.get("mode", "test"),
                "score": sess.score or 0,
                "max_score": sess.max_score,
                "pct": (sess.score or 0) / sess.max_score * 100,
            })
        return history

    # ------------------------------------------------------------------
    # Leaderboard & admin analytics
    # ------------------------------------------------------------------

    async def get_leaderboard(self, limit: int = 10, min_tests: int = 3) -> list[dict]:
        """Opted-in users ranked by average test score (min_tests required)."""
        result = await self.session.execute(
            select(
                User.telegram_id, User.first_name, User.username,
                User.current_streak,
                func.count(ExerciseSession.id).label("tests"),
                func.avg(_PCT).label("avg_pct"),
                func.max(_PCT).label("best_pct"),
            )
            .join(ExerciseSession, ExerciseSession.user_id == User.id)
            .where(User.leaderboard_opt_in.is_(True), _IS_SCORED_TEST)
            .group_by(User.id)
            .having(func.count(ExerciseSession.id) >= min_tests)
            .order_by(func.avg(_PCT).desc())
            .limit(limit)
        )
        return [
            {
                "telegram_id": r.telegram_id,
                "name": r.first_name or r.username or "Anonymous",
                "streak": r.current_streak or 0,
                "tests": r.tests,
                "avg_pct": r.avg_pct or 0.0,
                "best_pct": r.best_pct or 0.0,
            }
            for r in result
        ]

    async def get_global_stats(self) -> dict:
        """Bot-wide overview for the admin dashboard."""
        now = utcnow()
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)

        total_users = (await self.session.execute(
            select(func.count()).select_from(User)
        )).scalar_one()
        new_week = (await self.session.execute(
            select(func.count()).select_from(User).where(User.created_at >= week_ago)
        )).scalar_one()
        active_day = (await self.session.execute(
            select(func.count()).select_from(User).where(User.last_active_at >= day_ago)
        )).scalar_one()
        active_week = (await self.session.execute(
            select(func.count()).select_from(User).where(User.last_active_at >= week_ago)
        )).scalar_one()
        tests_total = (await self.session.execute(
            select(func.count()).select_from(ExerciseSession).where(_IS_SCORED_TEST)
        )).scalar_one()
        tests_week = (await self.session.execute(
            select(func.count()).select_from(ExerciseSession)
            .where(_IS_SCORED_TEST, ExerciseSession.started_at >= week_ago)
        )).scalar_one()
        avg_score = (await self.session.execute(
            select(func.avg(_PCT)).where(_IS_SCORED_TEST)
        )).scalar_one()

        return {
            "total_users": total_users,
            "new_users_week": new_week,
            "active_day": active_day,
            "active_week": active_week,
            "tests_total": tests_total,
            "tests_week": tests_week,
            "avg_score": avg_score or 0.0,
        }

    async def get_users_progress(self, limit: int = 30) -> list[dict]:
        """Per-user progress overview for admin, most recently active first."""
        result = await self.session.execute(
            select(
                User.telegram_id, User.first_name, User.username,
                User.current_streak, User.longest_streak, User.last_active_at,
                func.count(ExerciseSession.id).label("tests"),
                func.avg(_PCT).label("avg_pct"),
                func.max(_PCT).label("best_pct"),
            )
            .outerjoin(
                ExerciseSession,
                and_(ExerciseSession.user_id == User.id, _IS_SCORED_TEST),
            )
            .group_by(User.id)
            .order_by(User.last_active_at.desc())
            .limit(limit)
        )
        return [
            {
                "telegram_id": r.telegram_id,
                "name": r.first_name or r.username or str(r.telegram_id),
                "streak": r.current_streak or 0,
                "longest_streak": r.longest_streak or 0,
                "last_active": r.last_active_at,
                "tests": r.tests or 0,
                "avg_pct": r.avg_pct,
                "best_pct": r.best_pct,
            }
            for r in result
        ]

    async def get_sessions_for_export(self) -> list[dict]:
        """
        All scored sessions (including retry rounds, tagged in "mode") joined
        with user info, for CSV export — filter by mode in the spreadsheet.
        """
        result = await self.session.execute(
            select(
                User.telegram_id, User.first_name, User.username,
                ExerciseSession.exercise_type, ExerciseSession.difficulty,
                ExerciseSession.parameters, ExerciseSession.score,
                ExerciseSession.max_score, ExerciseSession.started_at,
            )
            .join(User, ExerciseSession.user_id == User.id)
            .where(_HAS_SCORE)
            .order_by(ExerciseSession.started_at)
        )
        rows = []
        for r in result:
            params = r.parameters or {}
            rows.append({
                "telegram_id": r.telegram_id,
                "name": r.first_name or r.username or "",
                "exercise": r.exercise_type.value,
                "difficulty": r.difficulty,
                "mode": params.get("mode", "test"),
                "pairs": params.get("count", ""),
                "score": r.score,
                "max_score": r.max_score,
                "pct": round(r.score / r.max_score * 100, 1) if r.max_score else "",
                "date": r.started_at.isoformat() if r.started_at else "",
            })
        return rows


class UserSkillRepository:
    """Repository for per-user skill XP bars."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, user_id: int, skill: str) -> UserSkill:
        result = await self.session.execute(
            select(UserSkill).where(
                UserSkill.user_id == user_id, UserSkill.skill == skill,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = UserSkill(user_id=user_id, skill=skill, xp=0, level=1, hard_streak=0)
            self.session.add(row)
            await self.session.flush()
        return row

    async def get_all_for_user(self, user_id: int) -> list[UserSkill]:
        result = await self.session.execute(
            select(UserSkill).where(UserSkill.user_id == user_id)
            .order_by(UserSkill.skill)
        )
        return list(result.scalars().all())

    async def add_xp(
        self, user_id: int, skill: str, amount: int,
        new_level: int, hard_streak: int,
    ) -> UserSkill:
        """Apply an XP gain (level precomputed via gamification.xp.level_from_xp)."""
        row = await self.get_or_create(user_id, skill)
        row.xp += amount
        row.level = new_level
        row.hard_streak = hard_streak
        await self.session.flush()
        return row


class BotSettingsRepository:
    """Key-value runtime settings (feature flags)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, key: str) -> Optional[str]:
        result = await self.session.execute(
            select(BotSetting.value).where(BotSetting.key == key)
        )
        return result.scalar_one_or_none()

    async def set(self, key: str, value: str) -> None:
        result = await self.session.execute(
            select(BotSetting).where(BotSetting.key == key)
        )
        row = result.scalar_one_or_none()
        if row is None:
            self.session.add(BotSetting(key=key, value=value))
        else:
            row.value = value
        await self.session.flush()


class AchievementRepository:
    """Repository for unlocked user achievements."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_unlocked_codes(self, user_id: int) -> set[str]:
        result = await self.session.execute(
            select(UserAchievement.code).where(UserAchievement.user_id == user_id)
        )
        return {row[0] for row in result}

    async def get_unlocked(self, user_id: int) -> dict[str, datetime]:
        """Map of code -> unlocked_at for a user."""
        result = await self.session.execute(
            select(UserAchievement.code, UserAchievement.unlocked_at)
            .where(UserAchievement.user_id == user_id)
        )
        return {code: ts for code, ts in result}

    async def unlock(self, user_id: int, codes: list[str]) -> None:
        for code in codes:
            self.session.add(UserAchievement(user_id=user_id, code=code))
        await self.session.flush()
