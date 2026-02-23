"""
Exercise Registry.
Central management of all available exercises.
"""

from typing import Dict, Type, Optional
from .base import BaseExercise
from .word_memorization import WordMemorizationExercise


class ExerciseRegistry:
    """
    Registry for all available exercises.
    Provides centralized access and management.
    """
    
    _exercises: Dict[str, Type[BaseExercise]] = {}
    _instances: Dict[str, BaseExercise] = {}
    
    @classmethod
    def register(cls, exercise_class: Type[BaseExercise]) -> Type[BaseExercise]:
        """
        Register an exercise class.
        Can be used as a decorator.
        """
        cls._exercises[exercise_class.exercise_type] = exercise_class
        return exercise_class
    
    @classmethod
    def get(cls, exercise_type: str) -> Optional[BaseExercise]:
        """Get an exercise instance by type."""
        if exercise_type not in cls._instances:
            exercise_class = cls._exercises.get(exercise_type)
            if exercise_class:
                cls._instances[exercise_type] = exercise_class()
        
        return cls._instances.get(exercise_type)
    
    @classmethod
    def get_all(cls) -> Dict[str, BaseExercise]:
        """Get all registered exercise instances."""
        for exercise_type in cls._exercises:
            if exercise_type not in cls._instances:
                cls._instances[exercise_type] = cls._exercises[exercise_type]()
        
        return cls._instances
    
    @classmethod
    def list_exercises(cls) -> list[dict]:
        """List all available exercises with metadata."""
        exercises = []
        for exercise_type, exercise_class in cls._exercises.items():
            exercises.append({
                "type": exercise_type,
                "name": exercise_class.name,
                "description": exercise_class.description
            })
        return exercises


# Register built-in exercises
ExerciseRegistry.register(WordMemorizationExercise)

# Future exercises can be registered like:
# ExerciseRegistry.register(NumberSequenceExercise)
# ExerciseRegistry.register(PatternRecognitionExercise)
