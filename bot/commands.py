"""
User-facing slash commands (/start, /stats, /history, ...) and their
inline-menu twins (menu:, lb:, settings: callbacks). No quiz logic here —
that's quiz_engine.py; exercise flows live in their own modules.
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import (
    get_session, UserRepository, ExerciseSessionRepository,
    AchievementRepository, UserSkillRepository,
)
from exercises import ExerciseRegistry
from gamification import (
    ACHIEVEMENTS, SKILLS, level_from_xp, render_progress_bar,
)
from exercises.word_memorization import QUESTION_TIME_LIMIT, DIFF_EMOJI
from .features import is_xp_enabled, is_exercise_enabled
from .quiz_engine import cancel_question_timer
from .state import get_user_state, clear_user_state, cleanup_bot_messages

logger = logging.getLogger(__name__)


# ============================================================================
# Main menu keyboard (shared with the callback router)
# ============================================================================

def get_main_menu_keyboard(show_placement: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    if show_placement:
        buttons.append([InlineKeyboardButton(
            "📏 Find your level (2 min)", callback_data="placement:start",
        )])
    for info in ExerciseRegistry.list_exercises():
        if not is_exercise_enabled(info["feature_flag"]):
            continue
        buttons.append([InlineKeyboardButton(
            f"{info['menu_emoji']} {info['name']}", callback_data=f"exercise:{info['type']}",
        )])
    buttons.append([
        InlineKeyboardButton("🏅 Achievements", callback_data="menu:achievements"),
        InlineKeyboardButton("🏆 Leaderboard", callback_data="menu:leaderboard"),
    ])
    return InlineKeyboardMarkup(buttons)


# ============================================================================
# Command Handlers
# ============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    cancel_question_timer(context, user.id)
    state = get_user_state(context)
    await cleanup_bot_messages(context.bot, update.effective_chat.id, state)

    async with get_session() as session:
        user_repo = UserRepository(session)
        db_user, created = await user_repo.get_or_create(
            telegram_id=user.id, username=user.username,
            first_name=user.first_name, last_name=user.last_name,
            language_code=user.language_code or "en",
        )
        if created:
            logger.info(f"New user registered: {user.id} ({user.username})")
        tests_done = await ExerciseSessionRepository(session).count_completed_tests(db_user.id)

    clear_user_state(context)

    welcome_text = (
        f"👋 Welcome to *Mental Training Bot*, {user.first_name}!\n\n"
        "This bot helps you train your mind with various exercises.\n\n"
        "🧠 *Available Exercises:*\n"
    )
    for info in ExerciseRegistry.list_exercises():
        if not is_exercise_enabled(info["feature_flag"]):
            continue
        welcome_text += f"• {info['name']}: {info['description']}\n"
    show_placement = tests_done == 0
    if show_placement:
        welcome_text += "\n📏 New here? Take a 2-minute level test to find your starting difficulty."
    welcome_text += "\nChoose an exercise to get started:"

    await update.message.reply_text(
        welcome_text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_keyboard(show_placement=show_placement),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    level_line = "/level - Your XP and skill levels\n" if is_xp_enabled() else ""
    help_text = (
        "🆘 *Mental Training Bot Help*\n\n"
        "*Commands:*\n"
        "/start - Main menu\n"
        "/help - This help message\n"
        "/stats - Your training statistics\n"
        "/history - Last 10 test results\n"
        f"{level_line}"
        "/achievements - Your achievements\n"
        "/leaderboard - Compare with others (opt-in)\n"
        "/exercises - Available exercises\n"
        "/settings - Preferences (compact results)\n\n"
        "*Formats:*\n"
        "🔗 *Word Pairs* — memorize pairs, recall the partner\n"
        "📜 *Word List* — memorize an ordered list, recall neighbors\n\n"
        "*Modes:*\n"
        "📝 *Training* — Study at your own pace\n"
        "🎯 *Test* — Study, then quiz (normal or ⚡ speed)\n"
        "🔀 *Reverse Quiz* — Re-quiz flipped\n\n"
        f"⏱ {QUESTION_TIME_LIMIT}s per question · typos tolerated"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        session_repo = ExerciseSessionRepository(session)
        user_repo = UserRepository(session)
        db_user = await user_repo.get_by_telegram_id(user.id)
        if not db_user:
            await update.message.reply_text("No statistics yet. Start training first!")
            return
        stats = await session_repo.get_user_stats(db_user.id)
        skill_rows = (
            await UserSkillRepository(session).get_all_for_user(db_user.id)
            if is_xp_enabled() else []
        )

    if stats["tests_total"] == 0:
        await update.message.reply_text(
            "📊 *Your Statistics*\n\nNo tests yet.\nComplete your first test to begin tracking!",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    streak = db_user.current_streak or 0
    longest = db_user.longest_streak or 0
    streak_line = f"🔥 *Streak:* {streak} day{'s' if streak != 1 else ''}"
    if longest > streak:
        streak_line += f"  (best: {longest})"
    text = f"📊 *Your Training Statistics*\n\n{streak_line}\n"
    for row in skill_rows:
        skill = SKILLS.get(row.skill)
        if skill:
            level, _, _ = level_from_xp(row.xp)
            text += f"{skill.emoji} *{skill.name}:* Level {level} · {row.xp:,} XP\n"

    text += f"\n🎯 *Tests: {stats['tests_total']}*\n"
    for d in ("beginner", "intermediate", "advanced"):
        row = stats["by_difficulty"].get(d)
        if not row:
            continue
        if row["mastered"]:
            mastery = f"mastered *{row['mastered']} pairs* ⭐"
        else:
            mastery = "no 90%+ yet"
        text += (
            f"{DIFF_EMOJI[d]} {d.capitalize()} — {row['tests']} test{'s' if row['tests'] != 1 else ''} · "
            f"{mastery}\n"
        )
    text += "_Mastered = biggest test scored 90%+_\n"
    if stats["latest_pairs"]:
        text += f"\nLatest: {stats['latest_pct']:.0f}% ({stats['latest_pairs']} pairs)"
    else:
        text += f"\nLatest score: {stats['latest_pct']:.0f}%"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show last 10 test sessions in a table."""
    user = update.effective_user
    async with get_session() as session:
        user_repo = UserRepository(session)
        session_repo = ExerciseSessionRepository(session)
        db_user = await user_repo.get_by_telegram_id(user.id)
        if not db_user:
            await update.message.reply_text("No history yet. Complete a test first!")
            return
        history = await session_repo.get_recent_test_history(db_user.id, limit=10)

    if not history:
        await update.message.reply_text(
            "📜 *Test History*\n\nNo test sessions yet.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = ["📜 *Test History (last 10)*\n"]
    for i, h in enumerate(history, 1):
        date_str = h["date"].strftime("%b %d, %H:%M") if h["date"] else "?"
        diff_emoji = DIFF_EMOJI.get(h["difficulty"], "⚪")
        reverse_tag = " 🔀" if h.get("mode") == "reverse" else ""
        unit = "words 📜" if h.get("format") == "list" else "pairs"
        lines.append(
            f"{i}. {date_str}  {diff_emoji} {h['count']} {unit}  "
            f"*{h['score']}/{h['max_score']}* ({h['pct']:.0f}%){reverse_tag}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def exercises_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "🧠 *Available Exercises:*\n\n"
    for info in ExerciseRegistry.list_exercises():
        if not is_exercise_enabled(info["feature_flag"]):
            continue
        text += f"*{info['name']}*\n{info['description']}\n\n"
    text += "Select an exercise from the menu below:"
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_keyboard(),
    )


async def level_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_xp_enabled():
        await update.message.reply_text("The XP system is not available right now.")
        return
    user = update.effective_user
    async with get_session() as session:
        db_user = await UserRepository(session).get_by_telegram_id(user.id)
        skill_rows = (
            await UserSkillRepository(session).get_all_for_user(db_user.id)
            if db_user else []
        )
    rows_by_code = {r.skill: r for r in skill_rows}

    lines = ["⭐ *Your Skills*\n"]
    for code, skill in SKILLS.items():
        row = rows_by_code.get(code)
        total_xp = row.xp if row else 0
        level, into, need = level_from_xp(total_xp)
        bar = render_progress_bar(into, need)
        lines.append(f"{skill.emoji} *{skill.name}* — Level {level}")
        lines.append(f"{bar}  {into}/{need} XP to next level")
        lines.append(f"_{skill.description}_ · Total: {total_xp:,} XP")
        if row and row.hard_streak >= 2:
            lines.append(f"🔥 Hard-exercise streak: {row.hard_streak} (XP bonus active)")
        lines.append("")
    lines.append(
        "💡 _Harder tests = more XP: more pairs, higher difficulty, speed mode, "
        "better accuracy.\nEasy tests give less XP as you level up._"
    )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ============================================================================
# Achievements
# ============================================================================

def _format_achievements(unlocked: dict) -> str:
    """Achievement list, phone-friendly: unlocked entries get the description
    and unlock date on their own indented lines."""
    unlocked_count = sum(1 for a in ACHIEVEMENTS if a.code in unlocked)
    lines = [f"🏅 *Achievements — {unlocked_count}/{len(ACHIEVEMENTS)}*\n"]
    for a in ACHIEVEMENTS:
        if a.code in unlocked:
            ts = unlocked[a.code]
            if lines[-1] and not lines[-1].endswith("\n"):
                lines.append("")
            lines.append(f"{a.emoji} *{a.name}* ✅")
            lines.append(f"      _{a.description}_")
            if ts:
                lines.append(f"      📅 {ts.strftime('%b %d, %Y')}")
            lines.append("")
        else:
            lines.append(f"🔒 {a.name} — {a.description}")
    return "\n".join(lines).rstrip()


async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        db_user = await UserRepository(session).get_by_telegram_id(user.id)
        unlocked = (
            await AchievementRepository(session).get_unlocked(db_user.id)
            if db_user else {}
        )
    await update.message.reply_text(
        _format_achievements(unlocked), parse_mode=ParseMode.MARKDOWN,
    )


# ============================================================================
# Leaderboard
# ============================================================================

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        db_user = await UserRepository(session).get_by_telegram_id(user.id)
        opted_in = bool(db_user and db_user.leaderboard_opt_in)
        board = await ExerciseSessionRepository(session).get_leaderboard(limit=10)

    text = _format_leaderboard(board, user.id)
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=_leaderboard_keyboard(opted_in),
    )


def _format_leaderboard(board: list[dict], viewer_telegram_id: int) -> str:
    lines = ["🏆 *Leaderboard* — avg test score (min 3 tests)\n"]
    if not board:
        lines.append("_No one on the board yet. Join and complete 3 tests!_")
    else:
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for rank, entry in enumerate(board, 1):
            marker = medals.get(rank, f"{rank}.")
            you = " ← you" if entry["telegram_id"] == viewer_telegram_id else ""
            streak = f" 🔥{entry['streak']}" if entry["streak"] else ""
            lines.append(
                f"{marker} *{entry['name']}* — {entry['avg_pct']:.0f}% avg, "
                f"{entry['best_pct']:.0f}% best ({entry['tests']} tests){streak}{you}"
            )
    lines.append("\n_Only users who opt in are listed._")
    return "\n".join(lines)


def _leaderboard_keyboard(opted_in: bool) -> InlineKeyboardMarkup:
    if opted_in:
        button = InlineKeyboardButton("🚪 Leave leaderboard", callback_data="lb:leave")
    else:
        button = InlineKeyboardButton("✋ Join leaderboard", callback_data="lb:join")
    return InlineKeyboardMarkup([[button]])


async def handle_leaderboard_callback(query, context, data: str) -> None:
    action = data.split(":")[1]
    opt_in = action == "join"
    user = query.from_user
    async with get_session() as session:
        user_repo = UserRepository(session)
        await user_repo.get_or_create(
            telegram_id=user.id, username=user.username,
            first_name=user.first_name, last_name=user.last_name,
        )
        await user_repo.set_leaderboard_opt_in(user.id, opt_in)
        board = await ExerciseSessionRepository(session).get_leaderboard(limit=10)

    text = _format_leaderboard(board, user.id)
    text += "\n\n✅ You joined the leaderboard!" if opt_in else "\n\n👋 You left the leaderboard."
    await query.edit_message_text(
        text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=_leaderboard_keyboard(opt_in),
    )


# ============================================================================
# Main-menu shortcuts (menu: callbacks)
# ============================================================================

async def handle_menu_callback(query, context, data: str) -> None:
    """Main-menu shortcuts to features that also exist as commands."""
    action = data.split(":")[1]
    user = query.from_user
    if action == "achievements":
        async with get_session() as session:
            db_user = await UserRepository(session).get_by_telegram_id(user.id)
            unlocked = (
                await AchievementRepository(session).get_unlocked(db_user.id)
                if db_user else {}
            )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]])
        await query.edit_message_text(
            _format_achievements(unlocked), parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
        )
    elif action == "leaderboard":
        async with get_session() as session:
            db_user = await UserRepository(session).get_by_telegram_id(user.id)
            opted_in = bool(db_user and db_user.leaderboard_opt_in)
            board = await ExerciseSessionRepository(session).get_leaderboard(limit=10)
        toggle = (
            InlineKeyboardButton("🚪 Leave leaderboard", callback_data="lb:leave")
            if opted_in
            else InlineKeyboardButton("✋ Join leaderboard", callback_data="lb:join")
        )
        kb = InlineKeyboardMarkup([
            [toggle],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ])
        await query.edit_message_text(
            _format_leaderboard(board, user.id),
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
        )


# ============================================================================
# Settings (/settings)
# ============================================================================

def _settings_text_and_keyboard(preferences: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Shared by /settings and the settings callback. To expose a toggle
    somewhere else (e.g. a button under test results), reuse this keyboard or
    send callback_data "settings:toggle_compact" from any other keyboard."""
    compact = preferences.get("compact_results", False)
    text = (
        "⚙️ *Settings*\n\n"
        "📋 *Compact test results*\n"
        "_On: results show only score, difficulty and the word pairs.\n"
        "Off: full results with streak, personal best and tips._\n\n"
        f"Currently: *{'On ✅' if compact else 'Off'}*"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📋 Compact results: {'✅ On' if compact else '⬜ Off'}",
            callback_data="settings:toggle_compact",
        )],
        [InlineKeyboardButton("📏 Retake level test", callback_data="placement:start")],
    ])
    return text, kb


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        db_user = await UserRepository(session).get_by_telegram_id(user.id)
        prefs = (db_user.preferences or {}) if db_user else {}
    text, kb = _settings_text_and_keyboard(prefs)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def handle_settings_callback(query, context, data: str) -> None:
    action = data.split(":")[1]
    user = query.from_user
    if action == "toggle_compact":
        async with get_session() as session:
            user_repo = UserRepository(session)
            db_user = await user_repo.get_by_telegram_id(user.id)
            if not db_user:
                await query.edit_message_text("Use /start first.")
                return
            current = (db_user.preferences or {}).get("compact_results", False)
            db_user = await user_repo.update_preferences(
                user.id, {"compact_results": not current}
            )
            prefs = db_user.preferences or {}
        text, kb = _settings_text_and_keyboard(prefs)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
