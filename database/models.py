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

    # Engaged training seconds for this round (study + quiz), measured from a
    # monotonic clock — NOT wall-clock presence in the chat. Real column so
    # "minutes trained" aggregates in SQL. NULL when unknown: training-mode
    # rows (no completion event to close them) and rounds whose start stamp
    # was lost to a bot restart. See bot/analytics.py.
    duration_s = Column(Integer, nullable=True)

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


class RedemptionCode(Base):
    """One-time access codes gating the bot to paid community members.

    duration_days NULL = lifetime access (subscription never expires).
    redeemed_by stores the Telegram ID (not users.id) so codes can be
    inspected without a join and survive user-row changes.
    Feature lives in bot/redeem.py — removing it: drop that module, this
    model, its repository, and the /redeem + /admin codes registrations.
    """
    __tablename__ = "redemption_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(32), unique=True, nullable=False, index=True)
    tier = Column(SQLEnum(SubscriptionTier), nullable=False)
    duration_days = Column(Integer, nullable=True)  # NULL = lifetime
    created_at = Column(DateTime, default=utcnow)
    redeemed_by = Column(BigInteger, nullable=True)  # Telegram ID
    redeemed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<RedemptionCode(code={self.code}, tier={self.tier}, redeemed_by={self.redeemed_by})>"


class ActivityEvent(Base):
    """One row per user interaction — the raw stream behind usage analytics.

    Telegram exposes no "app open/close" signal, only discrete events, so
    time-in-bot can only be reconstructed by sessionizing this stream with an
    idle-gap rule (see docs/ADMIN_GUIDE.md). Engaged *training* time is the
    trustworthy number and lives in ExerciseSession.duration_s instead.

    Stores telegram_id (not users.id) so logging costs exactly one INSERT with
    no user lookup on the hot path — same trade-off as RedemptionCode.
    Never stores message text: answers are content, and none of the analytics
    need them.

    Written by bot/analytics.py, gated by the analytics_enabled flag.
    """
    __tablename__ = "activity_events"
    __table_args__ = (
        Index("ix_activity_user_ts", "telegram_id", "ts"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    ts = Column(DateTime, default=utcnow, nullable=False)
    kind = Column(String(32), nullable=False)   # command | callback | message
    detail = Column(String(64), nullable=True)  # command name or callback prefix

    def __repr__(self):
        return f"<ActivityEvent(telegram_id={self.telegram_id}, kind={self.kind}, detail={self.detail})>"


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
