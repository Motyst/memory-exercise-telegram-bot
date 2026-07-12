"""
Per-user in-memory state shared by the handler modules.

State lives in PTB's context.user_data["state"] dict — one per user, reset on
bot restart. Job callbacks don't get user_data directly, so
get_job_user_state() digs it out of the application.
"""

import asyncio

from telegram.ext import ContextTypes

# With concurrent_updates enabled, two rapid messages from the same user could
# race through answer recording and double-score one question. Serialize
# answer recording per user.
_answer_locks: dict[int, asyncio.Lock] = {}


def get_answer_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _answer_locks:
        _answer_locks[user_id] = asyncio.Lock()
    return _answer_locks[user_id]


def get_user_state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "state" not in context.user_data:
        context.user_data["state"] = {}
    return context.user_data["state"]


def set_user_state(context: ContextTypes.DEFAULT_TYPE, key: str, value) -> None:
    state = get_user_state(context)
    state[key] = value


def clear_user_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["state"] = {}


def is_in_test_mode(context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = get_user_state(context)
    return state.get("test_active", False)


def get_job_user_state(application, user_id: int) -> dict:
    """State dict for *user_id* from a job/queue callback (no context.user_data)."""
    return application.user_data.get(user_id, {}).get("state", {})


# ---- Bot message tracking & cleanup (anti-clutter between quiz phases) ----

def track_bot_message(state: dict, message_id: int) -> None:
    if "bot_message_ids" not in state:
        state["bot_message_ids"] = []
    state["bot_message_ids"].append(message_id)


async def cleanup_bot_messages(bot, chat_id: int, state: dict) -> None:
    msg_ids = state.pop("bot_message_ids", [])
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
