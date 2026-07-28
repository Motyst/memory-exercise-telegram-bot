"""
Usage analytics: how long people actually train, and what they touch.

Two independent measures, deliberately kept apart:

1. **Engaged training time** — `ExerciseSession.duration_s`, measured from a
   monotonic clock between the start of a round and the moment its results are
   saved. This is the honest, user-facing number ("34 min trained this week"):
   it can't be inflated by leaving the chat open. Always recorded — the flag
   below does not gate it, since it's one column on a row that gets written
   anyway.

2. **Raw interaction stream** — one `activity_events` row per update. Telegram
   sends no app-open/close or idle signal, so "time in bot" can only ever be
   reconstructed by sessionizing this stream with an idle-gap rule (a gap
   longer than IDLE_GAP_MIN starts a new visit). Approximate by nature —
   admin-side analysis only, never quoted to users as their training time.
   Gated by the `analytics_enabled` flag (/admin analytics on|off).

Removal: delete this module, its import + TypeHandler registration in
bot/__init__.py, the `mark_round_start`/`round_duration_s` calls in
word_memo.py / quiz_engine.py / audio_viz.py, the ActivityEvent model +
ActivityEventRepository, the ANALYTICS_ENABLED_KEY flag and the
/admin analytics + /admin time subcommands. `duration_s` can stay: it's inert
without the calls that populate it.
"""

import asyncio
import logging
import time
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from database import get_session, ActivityEventRepository
from .features import is_flag_enabled, ANALYTICS_ENABLED_KEY

logger = logging.getLogger(__name__)

# State key holding the monotonic stamp for the round in progress.
ROUND_START_KEY = "round_started_at"

# A round longer than this is someone who walked away mid-quiz, not someone
# training — record nothing rather than poison the averages.
MAX_ROUND_SECONDS = 3600

# Gap between two events that ends a "visit" when sessionizing the raw stream.
# Not used by the bot itself — documented here so analysis code agrees with it.
IDLE_GAP_MIN = 5

# Fire-and-forget log tasks. Kept referenced so the event loop can't garbage
# collect a task mid-INSERT (asyncio only holds weak references).
_pending: set[asyncio.Task] = set()


# ---- Engaged training time -------------------------------------------------

def mark_round_start(state: dict) -> None:
    """Stamp the start of a round (study phase included, where there is one)."""
    state[ROUND_START_KEY] = time.monotonic()


def round_duration_s(state: dict, cap: Optional[int] = None) -> Optional[int]:
    """Seconds since mark_round_start, or None if unknown/implausible.

    Reads without popping: retry and reverse rounds re-stamp their own start,
    and a results screen may be rebuilt. Returns None when the stamp is
    missing (bot restarted mid-round) or the span exceeds the cap — better a
    gap in the data than a fake 40-minute training session.
    """
    started = state.get(ROUND_START_KEY)
    if started is None:
        return None
    elapsed = int(time.monotonic() - started)
    limit = min(cap, MAX_ROUND_SECONDS) if cap else MAX_ROUND_SECONDS
    if elapsed < 0 or elapsed > limit:
        return None
    return elapsed


# ---- Raw interaction stream ------------------------------------------------

def log_event(telegram_id: int, kind: str, detail: Optional[str] = None) -> None:
    """Queue one activity row. Never awaited by handlers, never raises."""
    if not is_flag_enabled(ANALYTICS_ENABLED_KEY):
        return
    task = asyncio.create_task(_write_event(telegram_id, kind, detail))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _write_event(telegram_id: int, kind: str, detail: Optional[str]) -> None:
    try:
        async with get_session() as session:
            await ActivityEventRepository(session).log(telegram_id, kind, detail)
    except Exception as e:  # analytics must never break a user's session
        logger.warning(f"Activity log failed ({kind}/{detail}): {e}")


async def activity_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """TypeHandler in group -1: sees every update before the real handlers.

    Message text is never stored — only its shape. Runs ahead of the feature
    handlers but does no awaiting of its own, so it adds no latency.
    """
    user = update.effective_user
    if user is None:
        return

    if update.callback_query and update.callback_query.data:
        # Prefix + action only ("word_memo:count"), not the full payload.
        parts = update.callback_query.data.split(":")
        log_event(user.id, "callback", ":".join(parts[:2]))
    elif update.message and update.message.text:
        text = update.message.text
        if text.startswith("/"):
            log_event(user.id, "command", text.split()[0].split("@")[0][:32])
        else:
            # Typed answers during a quiz look identical to stray chatter here;
            # both log as "message" with no content.
            log_event(user.id, "message")
