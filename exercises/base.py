"""
Base class for all mental training exercises.
Provides common interface and functionality.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from telegram import InlineKeyboardMarkup


class Difficulty(Enum):
    """Exercise difficulty levels."""
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


@dataclass
class ExerciseResult:
    """Result of generating an exercise."""
    text_content: str
    additional_data: Optional[dict] = None


class BaseExercise(ABC):
    """
    Abstract base class for all exercises.
    Implement this to add new exercise types.
    """
    
    # Class-level metadata
    name: str = "Base Exercise"
    description: str = "Base exercise class"
    exercise_type: str = "base"
    menu_emoji: str = "🧠"
    # Optional runtime feature-flag key (bot/features.py). None = always on;
    # set to a flag key to let admins pause the exercise for everyone.
    feature_flag: str | None = None
    
    def __init__(self):
        self.current_difficulty: Optional[Difficulty] = None
        self.current_parameters: dict = {}
    
    @abstractmethod
    def get_difficulty_keyboard(self) -> InlineKeyboardMarkup:
        """Return inline keyboard for difficulty selection."""
        pass
    
    @abstractmethod
    def get_parameter_keyboard(self, difficulty: Difficulty) -> InlineKeyboardMarkup:
        """Return inline keyboard for parameter selection after difficulty is chosen."""
        pass
    
    @abstractmethod
    async def generate(
        self, 
        difficulty: Difficulty, 
        parameters: dict
    ) -> ExerciseResult:
        """
        Generate the exercise content.
        
        Args:
            difficulty: Selected difficulty level
            parameters: Exercise-specific parameters (e.g., word count)
            
        Returns:
            ExerciseResult with text and optional image
        """
        pass
    
    @abstractmethod
    def get_intro_message(self) -> str:
        """Return introduction/instructions for the exercise."""
        pass
    
    def get_completion_keyboard(self) -> InlineKeyboardMarkup:
        """Return keyboard shown after exercise completion."""
        from telegram import InlineKeyboardButton
        
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Another List", callback_data=f"{self.exercise_type}:again"),
                InlineKeyboardButton("⚙️ Change Settings", callback_data=f"{self.exercise_type}:settings")
            ],
            [
                InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
            ]
        ])
