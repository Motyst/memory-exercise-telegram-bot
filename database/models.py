"""
Database models for the Mental Training Bot.
Supports user management, subscriptions, and exercise progress tracking.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float, Date,
    ForeignKey, JSON, Enum as SQLEnum, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship
import enum

Base = declarative_base()


class SubscriptionTier(enum.Enum):
    """User subscription levels."""
    FREE = "free"
    BASIC = "basic"
    PREMIUM = "premium"


class User(Base):
    """User model storing Telegram user information."""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
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
    
    # Streak
    current_streak = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)
    last_trained_date = Column(Date, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_active_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    exercise_sessions = relationship("ExerciseSession", back_populates="user")
    sr_cards = relationship("SpacedRepetitionCard", back_populates="user")

    def __repr__(self):
        return f"<User(telegram_id={self.telegram_id}, username={self.username})>"


class ExerciseType(enum.Enum):
    """Types of mental training exercises."""
    WORD_MEMORIZATION = "word_memorization"
    NUMBER_SEQUENCE = "number_sequence"
    PATTERN_RECOGNITION = "pattern_recognition"
    MENTAL_MATH = "mental_math"
    # Add more exercise types as needed


class ExerciseSession(Base):
    """Tracks individual exercise sessions for progress monitoring."""
    __tablename__ = "exercise_sessions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # Exercise details
    exercise_type = Column(SQLEnum(ExerciseType), nullable=False)
    difficulty = Column(String(50), nullable=False)  # e.g., "beginner", "intermediate", "advanced"
    
    # Session parameters (flexible JSON for different exercise types)
    parameters = Column(JSON, default=dict)  # e.g., {"word_count": 20, "word_types": ["nouns", "verbs"]}
    
    # Results (optional, for when we add quiz/verification)
    score = Column(Integer, nullable=True)
    max_score = Column(Integer, nullable=True)
    completed = Column(Boolean, default=False)
    
    # Timestamps
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="exercise_sessions")
    
    def __repr__(self):
        return f"<ExerciseSession(user_id={self.user_id}, type={self.exercise_type}, difficulty={self.difficulty})>"


class SpacedRepetitionCard(Base):
    """
    Tracks SM-2 spaced repetition state for individual word pairs per user.
    word1/word2 stored in canonical form (alphabetically sorted) for stable identity.
    """
    __tablename__ = "sr_cards"
    __table_args__ = (
        UniqueConstraint("user_id", "word1", "word2", name="uq_sr_card"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Canonical pair (word1 <= word2 alphabetically)
    word1 = Column(String(255), nullable=False)
    word2 = Column(String(255), nullable=False)

    # SM-2 state
    easiness_factor = Column(Float, default=2.5)   # EF, min 1.3
    interval_days = Column(Integer, default=1)      # days until next review
    repetitions = Column(Integer, default=0)        # consecutive correct reviews

    # Scheduling
    next_review_at = Column(DateTime, nullable=True)   # None = brand new card
    last_reviewed_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="sr_cards")

    def __repr__(self):
        return (
            f"<SRCard(user={self.user_id}, "
            f"pair={self.word1}/{self.word2}, "
            f"interval={self.interval_days}d, reps={self.repetitions})>"
        )
