"""
Repository pattern for database operations.
Provides clean interface for CRUD operations on models.
"""

from datetime import datetime, timedelta, date
from typing import Optional
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User, ExerciseSession, ExerciseType, SubscriptionTier, SpacedRepetitionCard


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


# ============================================================================
# Spaced Repetition (SM-2)
# ============================================================================

def _canonical_pair(w1: str, w2: str) -> tuple[str, str]:
    """Return pair with words in alphabetical order for stable DB identity."""
    if w1.lower() <= w2.lower():
        return w1, w2
    return w2, w1


def _sm2_quality(correct: bool, fuzzy: bool, answer: str) -> int:
    """Map a test result to SM-2 quality score (0–5)."""
    if not correct:
        if answer in ("(timed out)", "(skipped)"):
            return 1   # complete blackout
        return 2       # wrong but attempted
    if fuzzy:
        return 3       # correct with difficulty
    return 4           # correct recall


def _sm2_update(
    ef: float, interval: int, reps: int, quality: int
) -> tuple[float, int, int, datetime]:
    """
    Apply one SM-2 iteration.
    Returns (new_ef, new_interval, new_reps, next_review_at).
    """
    if quality < 3:
        # Failed: restart repetitions, review tomorrow
        reps = 0
        interval = 1
    else:
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 6
        else:
            interval = round(interval * ef)
        reps += 1
        ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        ef = max(1.3, ef)

    next_review = datetime.utcnow() + timedelta(days=interval)
    return ef, interval, reps, next_review


class SpacedRepetitionRepository:
    """Repository for SM-2 spaced repetition card operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_due_cards(
        self, user_id: int, limit: int = 50,
    ) -> list[SpacedRepetitionCard]:
        """Cards whose next_review_at is in the past (due now)."""
        now = datetime.utcnow()
        result = await self.session.execute(
            select(SpacedRepetitionCard)
            .where(
                and_(
                    SpacedRepetitionCard.user_id == user_id,
                    SpacedRepetitionCard.next_review_at <= now,
                )
            )
            .order_by(SpacedRepetitionCard.next_review_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_due_count(self, user_id: int) -> int:
        """Count cards due for review right now."""
        return len(await self.get_due_cards(user_id, limit=10000))

    async def get_stats(self, user_id: int) -> dict:
        """Aggregate SR deck stats for a user."""
        now = datetime.utcnow()
        result = await self.session.execute(
            select(SpacedRepetitionCard).where(
                SpacedRepetitionCard.user_id == user_id
            )
        )
        cards = list(result.scalars().all())

        total = len(cards)
        due = sum(1 for c in cards if c.next_review_at and c.next_review_at <= now)
        mature = sum(1 for c in cards if c.interval_days >= 21)
        avg_interval = (
            sum(c.interval_days for c in cards) / total if total else 0.0
        )
        return {
            "total": total,
            "due": due,
            "mature": mature,
            "avg_interval": avg_interval,
        }

    async def update_cards_from_results(
        self,
        user_id: int,
        results: list[dict],
        pairs: list[tuple[str, str]],
    ) -> None:
        """
        Create or update SR cards for every answered pair.
        results: list of result dicts from _record_answer
        pairs:   original pairs list indexed by pair_index
        """
        now = datetime.utcnow()
        for res in results:
            w1, w2 = pairs[res["pair_index"]]
            canon_w1, canon_w2 = _canonical_pair(w1, w2)
            quality = _sm2_quality(
                res["correct"], res.get("fuzzy", False), res["answer"]
            )

            existing = await self.session.execute(
                select(SpacedRepetitionCard).where(
                    and_(
                        SpacedRepetitionCard.user_id == user_id,
                        SpacedRepetitionCard.word1 == canon_w1,
                        SpacedRepetitionCard.word2 == canon_w2,
                    )
                )
            )
            card = existing.scalar_one_or_none()

            if card is None:
                new_ef, new_int, new_reps, next_rev = _sm2_update(
                    2.5, 1, 0, quality
                )
                card = SpacedRepetitionCard(
                    user_id=user_id,
                    word1=canon_w1,
                    word2=canon_w2,
                    easiness_factor=new_ef,
                    interval_days=new_int,
                    repetitions=new_reps,
                    next_review_at=next_rev,
                    last_reviewed_at=now,
                )
                self.session.add(card)
            else:
                new_ef, new_int, new_reps, next_rev = _sm2_update(
                    card.easiness_factor,
                    card.interval_days,
                    card.repetitions,
                    quality,
                )
                card.easiness_factor = new_ef
                card.interval_days = new_int
                card.repetitions = new_reps
                card.next_review_at = next_rev
                card.last_reviewed_at = now

        await self.session.flush()
