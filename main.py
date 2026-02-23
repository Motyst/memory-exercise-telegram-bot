#!/usr/bin/env python3
"""
Mental Training Bot - Main Entry Point

A Telegram bot for mental training exercises.
"""

import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from bot import create_application
from config import get_settings


def setup_logging() -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log")
        ]
    )
    
    # Reduce noise from external libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


def main() -> None:
    """Main entry point."""
    setup_logging()
    logger = logging.getLogger(__name__)
    
    # Check configuration
    settings = get_settings()
    if settings.telegram_bot_token == "YOUR_BOT_TOKEN_HERE":
        logger.error(
            "Bot token not configured!\n"
            "Please set TELEGRAM_BOT_TOKEN in your .env file or environment.\n"
            "Get a token from @BotFather on Telegram."
        )
        sys.exit(1)
    
    logger.info("Starting Mental Training Bot...")
    
    # Create and run application
    application = create_application()
    
    # Run the bot
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
