"""
Application configuration settings.
Uses environment variables with dotenv support.
"""

import os
from functools import lru_cache
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""
    
    def __init__(self):
        # Telegram
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
        
        # Database
        self.database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./mental_training.db")
        
        # Bot Settings
        self.bot_name = os.getenv("BOT_NAME", "MentalTrainingBot")
        self.max_word_pairs = int(os.getenv("MAX_WORD_PAIRS", "100"))
        self.default_word_pairs = int(os.getenv("DEFAULT_WORD_PAIRS", "10"))


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
