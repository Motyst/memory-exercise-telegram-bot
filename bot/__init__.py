"""
Bot initialization and setup.
"""

import logging
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from config import get_settings
from database import init_db, close_db
from .handlers import (
    start_command,
    help_command,
    stats_command,
    history_command,
    exercises_command,
    callback_handler,
    text_message_handler,
)

logger = logging.getLogger(__name__)


def setup_handlers(application: Application) -> None:
    """Register all handlers with the application."""
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("exercises", exercises_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler)
    )
    logger.info("Handlers registered successfully")


async def on_startup(application: Application) -> None:
    logger.info("Starting Mental Training Bot...")
    await init_db()
    logger.info("Database initialized")
    bot_info = await application.bot.get_me()
    logger.info(f"Bot started: @{bot_info.username}")


async def on_shutdown(application: Application) -> None:
    logger.info("Shutting down Mental Training Bot...")
    await close_db()
    logger.info("Database connections closed")


def create_application() -> Application:
    settings = get_settings()
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )
    setup_handlers(application)
    return application
