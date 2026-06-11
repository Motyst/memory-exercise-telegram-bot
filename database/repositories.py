"""
Repository pattern for database operations.
Provides clean interface for CRUD operations on models.
"""

from datetime import datetime, timedelta, date
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User, ExerciseSession, ExerciseType, SubscriptionTier


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
            user.last_active_at = datetime.utcnow()
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
            current_prefs = user.preferences or {}
            current_prefs.update(preferences)
            user.preferences = current_prefs
            await self.session.flush()
        return user


class ExerciseSessionRepository:
    """Repository for ExerciseSession operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, user_id: int, exercise_type: ExerciseType,
        difficulty: str, parameters: dict,
    ) -> ExerciseSession:
        exercise_session = ExerciseSession(
            user_id=user_id, exercise_type=exercise_type,
            difficulty=difficulty, parameters=parameters,
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
        """Get aggregated user statistics including test scores."""
        sessions = await self.get_user_sessions(user_id, limit=10000)

        stats = {
            "total_sessions": len(sessions),
            "by_type": {},
            "by_difficulty": {},
            "test_sessions": 0,
            "avg_score": 0.0,
            "best_score": 0.0,
            "latest_score": 0.0,
        }

        score_percentages: list[float] = []
        latest_test_session = None

        for sess in sessions:
            type_name = sess.exercise_type.value
            stats["by_type"][type_name] = stats["by_type"].get(type_name, 0) + 1
            stats["by_difficulty"][sess.difficulty] = (
                stats["by_difficulty"].get(sess.difficulty, 0) + 1
            )

            params = sess.parameters or {}
            if params.get("mode") == "test" and params.get("max_score"):
                score = params.get("score", 0)
                max_score = params["max_score"]
                pct = (score / max_score) * 100 if max_score > 0 else 0.0
                score_percentages.append(pct)
                if latest_test_session is None:
                    latest_test_session = pct

        if score_percentages:
            stats["test_sessions"] = len(score_percentages)
            stats["avg_score"] = sum(score_percentages) / len(score_percentages)
            stats["best_score"] = max(score_percentages)
            stats["latest_score"] = latest_test_session or 0.0

        return stats

    async def get_personal_best(
        self, user_id: int, difficulty: str, count: int,
    ) -> float | None:
        """
        Return the best score percentage for a given difficulty + pair count,
        or None if no previous test exists for that combo.
        """
        sessions = await self.get_user_sessions(user_id, limit=10000)
        best = None
        for sess in sessions:
            params = sess.parameters or {}
            if (
                params.get("mode") == "test"
                and sess.difficulty == difficulty
                and params.get("count") == count
                and params.get("max_score")
            ):
                pct = (params["score"] / params["max_score"]) * 100
                if best is None or pct > best:
                    best = pct
        return best

    async def get_recent_test_history(
        self, user_id: int, limit: int = 10,
    ) -> list[dict]:
        """
        Return the last *limit* test-mode sessions as a list of dicts:
        [{date, difficulty, count, score, max_score, pct}, ...]
        Newest first.
        """
        sessions = await self.get_user_sessions(user_id, limit=10000)
        history = []
        for sess in sessions:
            params = sess.parameters or {}
            if params.get("mode") == "test" and params.get("max_score"):
                history.append({
                    "date": sess.started_at,
                    "difficulty": sess.difficulty,
                    "count": params.get("count", "?"),
                    "score": params.get("score", 0),
                    "max_score": params["max_score"],
                    "pct": (params["score"] / params["max_score"]) * 100,
                })
            if len(history) >= limit:
                break
        return history
