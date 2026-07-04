"""
Runtime feature flags — DB-persisted, cached in memory.

The cache means zero per-message DB cost: flags load once at startup and are
updated in place when an admin toggles them. Safe because the bot is a single
process; revisit if it ever runs multiple workers.
"""

import logging

from database import get_session, BotSettingsRepository

logger = logging.getLogger(__name__)

XP_ENABLED_KEY = "xp_enabled"

# Defaults apply when the key has never been set in the DB.
_flags: dict[str, bool] = {XP_ENABLED_KEY: True}


async def load_feature_flags() -> None:
    """Call once at startup."""
    async with get_session() as session:
        repo = BotSettingsRepository(session)
        value = await repo.get(XP_ENABLED_KEY)
    if value is not None:
        _flags[XP_ENABLED_KEY] = value == "1"
    logger.info(f"Feature flags loaded: xp_enabled={_flags[XP_ENABLED_KEY]}")


def is_xp_enabled() -> bool:
    return _flags[XP_ENABLED_KEY]


async def set_xp_enabled(enabled: bool) -> None:
    async with get_session() as session:
        await BotSettingsRepository(session).set(
            XP_ENABLED_KEY, "1" if enabled else "0"
        )
    _flags[XP_ENABLED_KEY] = enabled
