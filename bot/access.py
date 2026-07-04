"""
Access control: admin checks and subscription-tier gating.

Subscription logic is not enforced anywhere yet — these helpers are the
foundation. To gate a feature later:

    user = await user_repo.get_by_telegram_id(update.effective_user.id)
    if not has_tier(user, SubscriptionTier.PREMIUM):
        await update.message.reply_text("This feature requires Premium.")
        return

Tiers are granted via /admin grant (manual for now); a payment provider can
call UserRepository.set_subscription() the same way later.
"""

from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from config import get_settings
from database import User, SubscriptionTier
from database.models import utcnow

_TIER_ORDER = {
    SubscriptionTier.FREE: 0,
    SubscriptionTier.BASIC: 1,
    SubscriptionTier.PREMIUM: 2,
}


def is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().admin_ids


def get_effective_tier(user: User | None) -> SubscriptionTier:
    """User's tier, downgraded to FREE if the subscription has expired."""
    if user is None or user.subscription_tier is None:
        return SubscriptionTier.FREE
    if (
        user.subscription_tier != SubscriptionTier.FREE
        and user.subscription_expires_at is not None
        and user.subscription_expires_at < utcnow()
    ):
        return SubscriptionTier.FREE
    return user.subscription_tier


def has_tier(user: User | None, required: SubscriptionTier) -> bool:
    return _TIER_ORDER[get_effective_tier(user)] >= _TIER_ORDER[required]


def admin_only(handler):
    """Decorator: silently ignore the command for non-admin users."""
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not is_admin(update.effective_user.id):
            return
        return await handler(update, context)
    return wrapper
