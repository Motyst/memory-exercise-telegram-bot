"""
Rolling anti-repeat window of recently shown words, stored in user
preferences. Used by the word-memo flow (generation) and the quiz engine
(saving after results) — lives in its own module so neither imports the other.
"""

from database import get_session, UserRepository

# How many recently used words to avoid re-showing.
RECENT_WORDS_WINDOW = 200


async def get_recent_words(telegram_id: int) -> list[str]:
    """Fetch rolling recent-words list from user preferences."""
    async with get_session() as session:
        user_repo = UserRepository(session)
        db_user = await user_repo.get_by_telegram_id(telegram_id)
        if not db_user:
            return []
        return (db_user.preferences or {}).get("recent_words", [])


async def save_recent_words(
    telegram_id: int, items: list, window: int = RECENT_WORDS_WINDOW
) -> None:
    """Append used words to the rolling recent-words window in user preferences.
    *items* is a list of pair tuples (pairs format) or plain words (list format)."""
    used = [
        w for item in items
        for w in (item if isinstance(item, (list, tuple)) else (item,))
    ]
    async with get_session() as session:
        user_repo = UserRepository(session)
        db_user = await user_repo.get_by_telegram_id(telegram_id)
        if not db_user:
            return
        prefs = dict(db_user.preferences or {})
        recent = prefs.get("recent_words", [])
        recent = (recent + used)[-window:]   # keep last N
        prefs["recent_words"] = recent
        await user_repo.update_preferences(telegram_id, prefs)
