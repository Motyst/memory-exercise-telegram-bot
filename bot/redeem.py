"""
Redemption codes — one-time access codes gating the bot to paid community
members (Skool). Generate a batch with /admin codes, hand codes out in the
paid community area, members activate with /redeem.

/redeem <code>                          — user side: activate a code
/admin codes <n> <tier> <days|lifetime> — generate a batch
/admin codes list                       — redeemed count + unredeemed codes

REMOVAL: this feature is self-contained. Delete this module, the
RedemptionCode model (database/models.py), RedemptionCodeRepository
(database/repositories.py + database/__init__.py exports), and these
one-line touch points: the /redeem CommandHandler + import (bot/__init__.py),
the "codes" subcommand branch + import (bot/admin.py), the /redeem
BotCommand line (bot/menu.py). The redemption_codes table can stay or be
dropped — nothing else reads it.
"""

import logging
from datetime import timedelta

from telegram import Update
from telegram.ext import ContextTypes

from database import (
    get_session, UserRepository, RedemptionCodeRepository, SubscriptionTier,
)
from database.models import utcnow

logger = logging.getLogger(__name__)


async def redeem_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/redeem <code> — activate an access code."""
    user = update.effective_user
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "🎟 To activate an access code, send:\n/redeem YOUR-CODE"
        )
        return

    code = args[0].strip().upper()
    async with get_session() as session:
        user_repo = UserRepository(session)
        await user_repo.get_or_create(
            telegram_id=user.id, username=user.username,
            first_name=user.first_name, last_name=user.last_name,
        )
        row = await RedemptionCodeRepository(session).redeem(code, user.id)
        if row is None:
            await update.message.reply_text(
                "❌ That code is invalid or already used.\n"
                "Check for typos and try again."
            )
            return
        expires_at = (
            utcnow() + timedelta(days=row.duration_days)
            if row.duration_days else None
        )
        await user_repo.set_subscription(user.id, row.tier, expires_at)

    if expires_at:
        until = f"until {expires_at.strftime('%B %d, %Y')}"
    else:
        until = "forever — founding member! 🏆"
    logger.info(f"Code {code} redeemed by {user.id} ({row.tier.value}, {until})")
    await update.message.reply_text(
        f"✅ Code accepted!\n"
        f"Your access: *{row.tier.value}* {until}\n\n"
        "Hit /start and train away 🧠",
        parse_mode="Markdown",
    )


async def admin_codes(update: Update, args: list[str]) -> None:
    """/admin codes ... — generate batches or list unredeemed codes.
    Caller (bot/admin.py) already enforced admin access."""
    usage = (
        "Usage:\n"
        "/admin codes <n> <free|basic|premium> <days|lifetime>\n"
        "/admin codes list"
    )
    if not args:
        await update.message.reply_text(usage)
        return

    if args[0].lower() == "list":
        async with get_session() as session:
            stats = await RedemptionCodeRepository(session).get_stats()
        lines = [f"🎟 Codes — {stats['redeemed']} redeemed, "
                 f"{len(stats['unredeemed'])} unredeemed\n"]
        for row in stats["unredeemed"]:
            dur = f"{row.duration_days}d" if row.duration_days else "lifetime"
            lines.append(f"`{row.code}` — {row.tier.value}, {dur}")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n…"
        await update.message.reply_text(text, parse_mode="Markdown")
        return

    try:
        n = int(args[0])
        tier = SubscriptionTier(args[1].lower())
        duration_days = None if args[2].lower() == "lifetime" else int(args[2])
        if not (1 <= n <= 200):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text(usage)
        return

    async with get_session() as session:
        codes = await RedemptionCodeRepository(session).create_batch(
            n, tier, duration_days,
        )

    dur = f"{duration_days} days" if duration_days else "lifetime"
    body = "\n".join(f"`{c}`" for c in codes)
    await update.message.reply_text(
        f"🎟 {n} codes generated — {tier.value}, {dur}:\n\n{body}",
        parse_mode="Markdown",
    )
