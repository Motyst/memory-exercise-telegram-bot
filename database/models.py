"""
Database models for the Mental Training Bot.
Supports user management, subscriptions, achievements, and exercise progress tracking.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, DateTime, Date,
    ForeignKey, JSON, Enum as SQLEnum, Index, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship
import enum

Base = declarative_base()


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite stores naive datetimes)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SubscriptionTier(enum.Enum):
    """User subscription levels."""
    FREE = "free"
    BASIC = "basic"
    PREMIUM = "premium"


class User(Base):
    """User model storing Telegram user information."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # BigInteger: Telegram user IDs already exceed 2^31 for newer accounts.
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    language_code = Column(String(10), default="en")

    # Subscription info
    subscription_tier = Column(
        SQLEnum(SubscriptionTier),
        default=SubscriptionTier.FREE
    )
    subscription_expires_at = Column(DateTime, nullable=True)

    # Preferences (stored as JSON for flexibility)
    preferences = Column(JSON, default=dict)

    # Gamification
    current_streak = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)
    last_trained_date = Column(Date, nullable=True)
    leaderboard_opt_in = Column(Boolean, default=False, nullable=False, server_default="0")

    # Timestamps
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    last_active_at = Column(DateTime, default=utcnow)

    # Relationships
    exercise_sessions = relationship("ExerciseSession", back_populates="user")
    achievements = relationship("UserAchievement", back_populates="user")

    def __repr__(self):
        return f"<User(telegram_id={self.telegram_id}, username={self.username})>"


class ExerciseType(enum.Enum):
    """Types of mental training exercises."""
    WORD_MEMORIZATION = "word_memorization"
    AUDIO_VISUALIZATION = "audio_visualization"
    NUMBER_SEQUENCE = "number_sequence"
    PATTERN_RECOGNITION = "pattern_recognition"
    MENTAL_MATH = "mental_math"
    # Add more exercise types as needed


class ExerciseSession(Base):
    """Tracks individual exercise sessions for progress monitoring."""
    __tablename__ = "exercise_sessions"
    __table_args__ = (
        Index("ix_sessions_user_started", "user_id", "started_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Exercise details
    exercise_type = Column(SQLEnum(ExerciseType), nullable=False)
    difficulty = Column(String(50), nullable=False)  # e.g., "beginner", "intermediate", "advanced"

    # Session parameters (flexible JSON for different exercise types)
    parameters = Column(JSON, default=dict)  # e.g., {"count": 20, "mode": "test"}

    # Results — real columns so stats/leaderboards can aggregate in SQL.
    score = Column(Integer, nullable=True)
    max_score = Column(Integer, nullable=True)
    completed = Column(Boolean, default=False)

    # Timestamps
    started_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="exercise_sessions")

    def __repr__(self):
        return f"<ExerciseSession(user_id={self.user_id}, type={self.exercise_type}, difficulty={self.difficulty})>"


class UserSkill(Base):
    """Per-user experience bar for one skill (e.g. "mnemonics").

    Future exercises map to their own skill code — adding a bar is just a new
    row per user, no schema change. XP math lives in gamification/xp.py.
    """
    __tablename__ = "user_skills"
    __table_args__ = (
        UniqueConstraint("user_id", "skill", name="uq_user_skill"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    skill = Column(String(50), nullable=False)
    xp = Column(Integer, default=0, nullable=False)
    level = Column(Integer, default=1, nullable=False)
    # Consecutive completed tests at/above the user's level (feeds streak bonus)
    hard_streak = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    def __repr__(self):
        return f"<UserSkill(user_id={self.user_id}, skill={self.skill}, xp={self.xp}, lvl={self.level})>"


class BotSetting(Base):
    """Key-value store for runtime-toggleable bot settings (e.g. xp_enabled)."""
    __tablename__ = "bot_settings"

    key = Column(String(50), primary_key=True)
    value = Column(String(255), nullable=False)


class UserAchievement(Base):
    """Unlocked achievements per user. Definitions live in gamification/achievements.py."""
    __tablename__ = "user_achievements"
    __table_args__ = (
        UniqueConstraint("user_id", "code", name="uq_achievement_user_code"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    code = Column(String(50), nullable=False)
    unlocked_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="achievements")

    def __repr__(self):
        return f"<UserAchievement(user_id={self.user_id}, code={self.code})>"
