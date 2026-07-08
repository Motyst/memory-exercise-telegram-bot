"""
Telegram Bot Handlers.
Handles all user interactions, commands, and callbacks.
"""

import asyncio
import logging
import random
import time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import (
    get_session, UserRepository, ExerciseSessionRepository,
    AchievementRepository, UserSkillRepository, ExerciseType,
)
from exercises import ExerciseRegistry, Difficulty
from gamification import (
    ACHIEVEMENTS, AchievementContext, evaluate_achievements,
    SKILLS, EXERCISE_SKILLS, compute_test_xp, level_from_xp,
    xp_for_next_level, render_progress_bar,
)
from .features import is_xp_enabled
from exercises.word_memorization import (
    SECONDS_PER_PAIR,
    SPEED_MODE_MULTIPLIER,
    QUESTION_TIME_LIMIT,
    DIFFICULTY_NAMES,
    is_fuzzy_match,
    get_progression_suggestion,
)

logger = logging.getLogger(__name__)

# If a text answer arrives within this many seconds after a question timed out,
# it was almost certainly typed for the timed-out question (the next question
# hasn't visibly rendered yet) — apply it retroactively instead of counting it
# against the new question.
ANSWER_TIMEOUT_GRACE = 2.0

# With concurrent_updates enabled, two rapid messages from the same user could
# race through _record_answer and double-score one question. Serialize answer
# recording per user.
_answer_locks: dict[int, asyncio.Lock] = {}


def _get_answer_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _answer_locks:
        _answer_locks[user_id] = asyncio.Lock()
    return _answer_locks[user_id]


# ============================================================================
# User State Management
# ============================================================================

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


# ============================================================================
# Bot Message Tracking & Cleanup
# ============================================================================

def _track_bot_message(state: dict, message_id: int) -> None:
    if "bot_message_ids" not in state:
        state["bot_message_ids"] = []
    state["bot_message_ids"].append(message_id)


async def _cleanup_bot_messages(bot, chat_id: int, state: dict) -> None:
    msg_ids = state.pop("bot_message_ids", [])
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass


# ============================================================================
# Command Handlers
# ============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    _cancel_question_timer(context, user.id)
    state = get_user_state(context)
    await _cleanup_bot_messages(context.bot, update.effective_chat.id, state)

    async with get_session() as session:
        user_repo = UserRepository(session)
        db_user, created = await user_repo.get_or_create(
            telegram_id=user.id, username=user.username,
            first_name=user.first_name, last_name=user.last_name,
            language_code=user.language_code or "en",
        )
        if created:
            logger.info(f"New user registered: {user.id} ({user.username})")

    clear_user_state(context)

    welcome_text = (
        f"👋 Welcome to *Mental Training Bot*, {user.first_name}!\n\n"
        "This bot helps you train your mind with various exercises.\n\n"
        "🧠 *Available Exercises:*\n"
    )
    for info in ExerciseRegistry.list_exercises():
        welcome_text += f"• {info['name']}: {info['description']}\n"
    welcome_text += "\nChoose an exercise to get started:"

    await update.message.reply_text(
        welcome_text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_keyboard(),
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
        "/exercises - Available exercises\n\n"
        "*Modes:*\n"
        "📝 *Training* — Study at your own pace\n"
        "🎯 *Test* — Study, then quiz (normal or ⚡ speed)\n"
        "🔀 *Reverse Quiz* — Re-quiz with columns flipped\n\n"
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

    if stats["total_sessions"] == 0:
        await update.message.reply_text(
            "📊 *Your Statistics*\n\nNo training sessions yet.\nStart your first exercise to begin tracking!",
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
    text += f"*Total Sessions:* {stats['total_sessions']}\n"
    if stats["by_type"]:
        text += "\n*By Exercise Type:*\n"
        for t, c in stats["by_type"].items():
            text += f"  • {t}: {c}\n"
    if stats["by_difficulty"]:
        text += "\n*By Difficulty:*\n"
        for d, c in stats["by_difficulty"].items():
            emoji = {"beginner": "🟢", "intermediate": "🟡", "advanced": "🔴"}.get(d, "⚪")
            text += f"  {emoji} {d.capitalize()}: {c}\n"
    if stats["test_sessions"] > 0:
        text += (
            "\n🎯 *Test Mode Scores:*\n"
            f"  Tests taken: {stats['test_sessions']}\n"
            f"  Average score: {stats['avg_score']:.0f}%\n"
            f"  Best score: {stats['best_score']:.0f}%\n"
            f"  Latest score: {stats['latest_score']:.0f}%\n"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show last 10 test sessions in a table (#7)."""
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
        diff_emoji = {"beginner": "🟢", "intermediate": "🟡", "advanced": "🔴"}.get(h["difficulty"], "⚪")
        lines.append(
            f"{i}. {date_str}  {diff_emoji} {h['count']} pairs  "
            f"*{h['score']}/{h['max_score']}* ({h['pct']:.0f}%)"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def exercises_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "🧠 *Available Exercises:*\n\n"
    for info in ExerciseRegistry.list_exercises():
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


async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with get_session() as session:
        db_user = await UserRepository(session).get_by_telegram_id(user.id)
        unlocked = (
            await AchievementRepository(session).get_unlocked(db_user.id)
            if db_user else {}
        )

    lines = [f"🏅 *Achievements — {len(unlocked)}/{len(ACHIEVEMENTS)}*\n"]
    for a in ACHIEVEMENTS:
        if a.code in unlocked:
            date_str = unlocked[a.code].strftime("%b %d, %Y")
            lines.append(f"{a.emoji} *{a.name}* — {a.description}  ✅ _{date_str}_")
        else:
            lines.append(f"🔒 {a.name} — {a.description}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


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
# Callback Query Handlers
# ============================================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.debug(f"Callback received: {data}")

    if data == "main_menu":
        await show_main_menu(query, context)
    elif data.startswith("exercise:"):
        await start_exercise(query, context, data.split(":")[1])
    else:
        # Route "<prefix>:..." to the registered handler for that prefix.
        # New exercises: add one entry to CALLBACK_ROUTES (see bottom of file).
        route = CALLBACK_ROUTES.get(data.split(":")[0])
        if route:
            await route(query, context, data)
        else:
            logger.warning(f"Unknown callback: {data}")


async def show_main_menu(query, context) -> None:
    _cancel_question_timer(context, query.from_user.id)
    state = get_user_state(context)
    await _cleanup_bot_messages(context.bot, query.message.chat_id, state)
    clear_user_state(context)
    await query.edit_message_text(
        "🧠 *Mental Training Bot*\n\nChoose an exercise to begin:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu_keyboard(),
    )


async def start_exercise(query, context, exercise_type: str) -> None:
    exercise = ExerciseRegistry.get(exercise_type)
    if not exercise:
        await query.edit_message_text("Exercise not found.")
        return
    _cancel_question_timer(context, query.from_user.id)
    state = get_user_state(context)
    await _cleanup_bot_messages(context.bot, query.message.chat_id, state)
    set_user_state(context, "current_exercise", exercise_type)
    kb = exercise.get_mode_keyboard()
    await query.edit_message_text(
        exercise.get_intro_message(),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=kb,
    )


async def handle_word_memo_callback(query, context, data: str) -> None:
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else None
    value = parts[2] if len(parts) > 2 else None
    exercise = ExerciseRegistry.get("word_memo")

    if action == "start":
        _cancel_question_timer(context, query.from_user.id)
        state = get_user_state(context)
        await _cleanup_bot_messages(context.bot, query.message.chat_id, state)
        clear_user_state(context)
        set_user_state(context, "current_exercise", "word_memo")
        kb = exercise.get_mode_keyboard()
        await query.edit_message_text(
            exercise.get_intro_message(), parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )

    elif action == "mode":
        set_user_state(context, "mode", value)
        await query.edit_message_text(
            exercise.get_difficulty_message(value), parse_mode=ParseMode.MARKDOWN,
            reply_markup=exercise.get_difficulty_keyboard(),
        )

    elif action == "diff":
        difficulty = Difficulty(value)
        set_user_state(context, "difficulty", difficulty)
        state = get_user_state(context)
        mode = state.get("mode", "training")

        if mode == "test":
            # Show speed selection for test mode (#4)
            await query.edit_message_text(
                exercise.get_speed_message(difficulty),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=exercise.get_speed_keyboard(),
            )
        else:
            # Training mode goes straight to count
            mode_label = "📝 Training"
            diff_label = DIFFICULTY_NAMES[difficulty]
            await query.edit_message_text(
                f"*Mode:* {mode_label}\n*Difficulty:* {diff_label}\n\nHow many word pairs?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=exercise.get_parameter_keyboard_training(difficulty),
            )

    elif action == "speed":
        # Speed selection (#4): value is "normal" or "fast"
        set_user_state(context, "speed_mode", value == "fast")
        state = get_user_state(context)
        difficulty = state.get("difficulty", Difficulty.BEGINNER)
        speed_label = "⚡ Speed" if value == "fast" else "🕐 Normal"
        diff_label = DIFFICULTY_NAMES[difficulty]
        await query.edit_message_text(
            f"*Mode:* 🎯 Test\n*Difficulty:* {diff_label}\n*Pace:* {speed_label}\n\nHow many word pairs?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=exercise.get_parameter_keyboard(difficulty),
        )

    elif action == "back_to_diff":
        state = get_user_state(context)
        mode = state.get("mode", "training")
        await query.edit_message_text(
            exercise.get_difficulty_message(mode), parse_mode=ParseMode.MARKDOWN,
            reply_markup=exercise.get_difficulty_keyboard(),
        )

    elif action == "back_to_speed":
        state = get_user_state(context)
        difficulty = state.get("difficulty", Difficulty.BEGINNER)
        await query.edit_message_text(
            exercise.get_speed_message(difficulty), parse_mode=ParseMode.MARKDOWN,
            reply_markup=exercise.get_speed_keyboard(),
        )

    elif action == "count":
        count = int(value)
        set_user_state(context, "count", count)
        state = get_user_state(context)
        difficulty = state.get("difficulty", Difficulty.BEGINNER)
        mode = state.get("mode", "training")
        if mode == "test":
            await generate_word_memo_test(query, context, difficulty, count)
        else:
            await generate_word_memo(query, context, difficulty, count)

    elif action == "again":
        state = get_user_state(context)
        difficulty = state.get("difficulty", Difficulty.BEGINNER)
        count = state.get("count", 10)
        mode = state.get("mode", "training")
        await _cleanup_bot_messages(context.bot, query.message.chat_id, state)
        if mode == "test":
            await generate_word_memo_test(query, context, difficulty, count)
        else:
            await generate_word_memo(query, context, difficulty, count)

    elif action == "settings":
        _cancel_question_timer(context, query.from_user.id)
        state = get_user_state(context)
        await _cleanup_bot_messages(context.bot, query.message.chat_id, state)
        clear_user_state(context)
        set_user_state(context, "current_exercise", "word_memo")
        kb = exercise.get_mode_keyboard()
        await query.edit_message_text(
            exercise.get_intro_message(), parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )

    elif action == "skip":
        _cancel_question_timer(context, query.from_user.id)
        await _record_answer(context, query.message.chat_id, "(skipped)")

    elif action == "retry_mistakes":
        await _start_retry_mistakes(query, context)

    elif action == "reverse_quiz":
        await _start_reverse_quiz(query, context)


# ============================================================================
# Training Mode
# ============================================================================

async def _get_recent_words(telegram_id: int) -> list[str]:
    """Fetch rolling recent-words list from user preferences."""
    async with get_session() as session:
        user_repo = UserRepository(session)
        db_user = await user_repo.get_by_telegram_id(telegram_id)
        if not db_user:
            return []
        return (db_user.preferences or {}).get("recent_words", [])


async def _save_recent_words(
    telegram_id: int, pairs: list[tuple[str, str]], window: int = 200
) -> None:
    """Append used words to the rolling recent-words window in user preferences."""
    used = [w for pair in pairs for w in pair]
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


async def generate_word_memo(query, context, difficulty, count) -> None:
    user = query.from_user
    exercise = ExerciseRegistry.get("word_memo")
    recent = await _get_recent_words(user.id)
    async with get_session() as session:
        user_repo = UserRepository(session)
        session_repo = ExerciseSessionRepository(session)
        db_user = await user_repo.get_by_telegram_id(user.id)
        if db_user:
            await session_repo.create(
                user_id=db_user.id, exercise_type=ExerciseType.WORD_MEMORIZATION,
                difficulty=difficulty.value, parameters={"count": count, "mode": "training"},
            )
    result = await exercise.generate(
        difficulty=difficulty, parameters={"count": count, "recent_words": recent}
    )
    await _save_recent_words(user.id, result.additional_data["pairs"])
    await query.edit_message_text(
        result.text_content, parse_mode=ParseMode.MARKDOWN,
        reply_markup=exercise.get_completion_keyboard(),
    )


# ============================================================================
# Test Mode
# ============================================================================

def _build_quiz_items(pairs):
    """Build shuffled quiz items with a random word from each pair shown."""
    quiz_order = list(range(len(pairs)))
    random.shuffle(quiz_order)
    quiz_items = []
    for idx in quiz_order:
        w1, w2 = pairs[idx]
        if random.choice([True, False]):
            shown, expected = w1, w2
        else:
            shown, expected = w2, w1
        quiz_items.append({"pair_index": idx, "shown_word": shown, "expected": expected})
    return quiz_items


async def generate_word_memo_test(query, context, difficulty, count) -> None:
    exercise = ExerciseRegistry.get("word_memo")
    recent = await _get_recent_words(query.from_user.id)
    result = await exercise.generate(
        difficulty=difficulty, parameters={"count": count, "recent_words": recent}
    )
    pairs = result.additional_data["pairs"]

    state = get_user_state(context)
    speed = state.get("speed_mode", False)
    multiplier = SPEED_MODE_MULTIPLIER if speed else 1.0
    countdown_seconds = int(count * SECONDS_PER_PAIR * multiplier)

    quiz_items = _build_quiz_items(pairs)

    set_user_state(context, "test_active", False)
    set_user_state(context, "test_exercise_type", exercise.exercise_type)
    set_user_state(context, "test_pairs", pairs)
    set_user_state(context, "test_quiz_items", quiz_items)
    set_user_state(context, "test_current_index", 0)
    set_user_state(context, "test_results", [])
    set_user_state(context, "test_difficulty", difficulty)
    set_user_state(context, "test_chat_id", query.message.chat_id)
    set_user_state(context, "baseline_results", [])

    study_text = exercise.format_pairs_text_for_test(
        pairs, difficulty, countdown_seconds, speed_mode=speed,
    )
    await query.edit_message_text(study_text, parse_mode=ParseMode.MARKDOWN)

    set_user_state(context, "test_study_message_id", query.message.message_id)
    _track_bot_message(state, query.message.message_id)

    context.job_queue.run_once(
        _start_quiz_after_timer, when=countdown_seconds,
        chat_id=query.message.chat_id, user_id=query.from_user.id,
        data={"user_id": query.from_user.id},
        name=f"quiz_timer_{query.from_user.id}",
    )


async def _start_quiz_after_timer(context) -> None:
    job = context.job
    chat_id, user_id = job.chat_id, job.data["user_id"]
    user_data = context.application.user_data.get(user_id, {})
    state = user_data.get("state", {})
    if not state.get("test_quiz_items"):
        return
    study_msg_id = state.get("test_study_message_id")
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=study_msg_id,
            text="⏱ *Time's up!* The quiz is starting now...",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(f"Could not edit study message: {e}")
    state["test_active"] = True
    await _send_next_question(context, chat_id, state, user_id)


# ---- Per-question timer ----

def _question_timer_name(user_id: int) -> str:
    return f"question_timer_{user_id}"


def _cancel_question_timer(context, user_id: int) -> None:
    for job in context.job_queue.get_jobs_by_name(_question_timer_name(user_id)):
        job.schedule_removal()


async def _question_timeout_callback(context) -> None:
    job = context.job
    chat_id, user_id = job.chat_id, job.data["user_id"]
    user_data = context.application.user_data.get(user_id, {})
    state = user_data.get("state", {})
    if not state.get("test_active"):
        return
    # Stale-timer guard: if the index already moved past the question this
    # timer was armed for, the user answered in time — don't time out the
    # next question.
    if state.get("test_current_index", 0) != job.data.get("question_index"):
        return
    state["last_timeout_at"] = time.monotonic()
    await _record_answer(context, chat_id, "(timed out)", user_id=user_id)


# ---- Core quiz flow ----

async def _send_next_question(context, chat_id, state, user_id=None) -> None:
    exercise = ExerciseRegistry.get(state.get("test_exercise_type", "word_memo"))
    current_index = state.get("test_current_index", 0)
    quiz_items = state.get("test_quiz_items", [])

    if current_index >= len(quiz_items):
        # If the final question just timed out, hold for the grace window so a
        # late answer can still be credited before results render (test_active
        # stays True during the sleep, so the answer routes through normally).
        last_timeout_at = state.get("last_timeout_at")
        if last_timeout_at is not None and time.monotonic() - last_timeout_at <= ANSWER_TIMEOUT_GRACE:
            await asyncio.sleep(ANSWER_TIMEOUT_GRACE)
        await _cleanup_bot_messages(context.bot, chat_id, state)
        await _show_test_results(context, chat_id, state)
        return

    item = quiz_items[current_index]
    prompt = exercise.format_test_prompt(item["shown_word"], current_index + 1, len(quiz_items))
    msg = await context.bot.send_message(
        chat_id=chat_id, text=prompt, parse_mode=ParseMode.MARKDOWN,
        reply_markup=exercise.get_skip_keyboard(QUESTION_TIME_LIMIT),
    )
    state["test_prompt_message_id"] = msg.message_id
    _track_bot_message(state, msg.message_id)

    if user_id is None:
        user_id = chat_id
    _cancel_question_timer(context, user_id)
    context.job_queue.run_once(
        _question_timeout_callback, when=QUESTION_TIME_LIMIT,
        chat_id=chat_id, user_id=user_id,
        data={"user_id": user_id, "question_index": current_index},
        name=_question_timer_name(user_id),
    )


async def _record_answer(context, chat_id, answer_text, user_id=None, answer_message_id=None) -> None:
    if user_id is None:
        user_id = chat_id
    async with _get_answer_lock(user_id):
        await _record_answer_impl(context, chat_id, answer_text, user_id, answer_message_id)


async def _record_answer_impl(context, chat_id, answer_text, user_id, answer_message_id) -> None:
    user_data = context.application.user_data.get(user_id, {})
    state = user_data.get("state", {})

    results = state.get("test_results", [])
    is_special = answer_text in ("(skipped)", "(timed out)")

    # Late-answer grace: the previous question just timed out and this text
    # arrived moments later — the user typed it for the timed-out question,
    # not the one that hasn't visibly appeared yet. Re-score the previous
    # result and leave the current question (and its timer) untouched.
    # Runs BEFORE the active/index guards so it also covers the final
    # question, whose results render immediately after timeout.
    last_timeout_at = state.get("last_timeout_at")
    if (
        not is_special
        and last_timeout_at is not None
        and time.monotonic() - last_timeout_at <= ANSWER_TIMEOUT_GRACE
        and results
        and results[-1]["answer"] == "(timed out)"
    ):
        state["last_timeout_at"] = None
        if answer_message_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=answer_message_id)
            except Exception:
                pass
        prev = results[-1]
        exact = answer_text.strip().lower() == prev["expected"].strip().lower()
        fuzzy = (not exact) and is_fuzzy_match(answer_text, prev["expected"])
        prev["answer"] = answer_text.strip()
        prev["correct"] = exact or fuzzy
        prev["fuzzy"] = fuzzy
        return

    if not state.get("test_active"):
        return

    quiz_items = state.get("test_quiz_items", [])
    current_index = state.get("test_current_index", 0)
    if current_index >= len(quiz_items):
        return
    item = quiz_items[current_index]

    _cancel_question_timer(context, user_id)

    if answer_message_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=answer_message_id)
        except Exception:
            pass

    if is_special:
        is_correct, fuzzy = False, False
    else:
        exact = answer_text.strip().lower() == item["expected"].strip().lower()
        fuzzy = (not exact) and is_fuzzy_match(answer_text, item["expected"])
        is_correct = exact or fuzzy

    results.append({
        "pair_index": item["pair_index"], "shown_word": item["shown_word"],
        "expected": item["expected"], "answer": answer_text.strip(),
        "correct": is_correct, "fuzzy": fuzzy,
    })
    state["test_results"] = results
    state["test_current_index"] = current_index + 1
    await _send_next_question(context, chat_id, state, user_id)


async def _show_test_results(context, chat_id, state) -> None:
    state["last_timeout_at"] = None
    exercise = ExerciseRegistry.get(state.get("test_exercise_type", "word_memo"))
    pairs = state.get("test_pairs", [])
    results = state.get("test_results", [])
    difficulty = state.get("test_difficulty", Difficulty.BEGINNER)
    baseline = state.get("baseline_results", [])

    # Merge baseline + retry results
    merged_by_pair = {r["pair_index"]: r for r in baseline}
    for r in results:
        merged_by_pair[r["pair_index"]] = r
    merged_results = list(merged_by_pair.values())

    correct_count = sum(1 for r in merged_results if r["correct"])
    total = len(pairs)
    score_pct = (correct_count / total * 100) if total > 0 else 0

    # Personal best check + streak update + achievements + XP
    personal_best_text = None
    streak_text = None
    new_achievements = []
    xp_lines = []
    try:
        async with get_session() as session:
            user_repo = UserRepository(session)
            session_repo = ExerciseSessionRepository(session)
            achievement_repo = AchievementRepository(session)
            db_user = await user_repo.get_by_telegram_id(chat_id)
            if db_user:
                difficulty_value = difficulty.value
                prev_best = await session_repo.get_personal_best(
                    db_user.id, difficulty_value, len(pairs),
                )
                # Save current session — score/max_score go into real columns
                # so stats, leaderboard and admin views aggregate in SQL.
                await session_repo.create(
                    user_id=db_user.id,
                    exercise_type=ExerciseType.WORD_MEMORIZATION,
                    difficulty=difficulty_value,
                    parameters={"count": len(pairs), "mode": "test"},
                    score=correct_count, max_score=total, completed=True,
                )
                # Personal best
                if prev_best is not None and score_pct > prev_best:
                    personal_best_text = f"🏆 *New personal best!* (previous: {prev_best:.0f}%)"
                elif prev_best is None:
                    personal_best_text = "🏆 *First test at this level — benchmark set!*"
                # Streak
                streak_info = await user_repo.update_streak(chat_id)
                if streak_info["is_first_today"]:
                    s = streak_info["streak"]
                    if s == 1:
                        streak_text = "📅 *Day 1 — streak started!*"
                    elif s == streak_info["longest"]:
                        streak_text = f"🔥 *{s}-day streak — new record!*"
                    else:
                        streak_text = f"🔥 *{s}-day streak!* Keep it up!"
                # Achievements
                total_tests = await session_repo.count_completed_tests(db_user.id)
                ctx = AchievementContext(
                    score_pct=score_pct,
                    pair_count=len(pairs),
                    difficulty=difficulty_value,
                    speed_mode=state.get("speed_mode", False),
                    total_tests=total_tests,
                    streak=streak_info["streak"],
                    longest_streak=streak_info["longest"],
                )
                unlocked = await achievement_repo.get_unlocked_codes(db_user.id)
                new_achievements = evaluate_achievements(ctx, unlocked)
                if new_achievements:
                    await achievement_repo.unlock(
                        db_user.id, [a.code for a in new_achievements]
                    )
                # XP — based on THIS round's questions (a fresh test or
                # reverse quiz = full set; retry-mistakes = just the retried
                # subset, so retries can't farm full-test XP).
                if is_xp_enabled():
                    skill_code = EXERCISE_SKILLS.get(ExerciseType.WORD_MEMORIZATION.value)
                    quiz_items = state.get("test_quiz_items", [])
                    if skill_code and quiz_items:
                        round_correct = sum(1 for r in results if r["correct"])
                        round_pct = round_correct / len(quiz_items) * 100
                        skill_repo = UserSkillRepository(session)
                        skill_row = await skill_repo.get_or_create(db_user.id, skill_code)
                        old_level = skill_row.level
                        xp_res = compute_test_xp(
                            pairs=len(quiz_items),
                            difficulty=difficulty_value,
                            speed_mode=state.get("speed_mode", False),
                            score_pct=round_pct,
                            level=skill_row.level,
                            hard_streak=skill_row.hard_streak,
                        )
                        new_level, xp_into, xp_need = level_from_xp(
                            skill_row.xp + xp_res.xp
                        )
                        await skill_repo.add_xp(
                            db_user.id, skill_code, xp_res.xp,
                            new_level, xp_res.new_hard_streak,
                        )
                        if xp_res.xp > 0:
                            skill = SKILLS[skill_code]
                            xp_lines.append(
                                f"⭐ *+{xp_res.xp} XP* {skill.emoji} {skill.name} — "
                                f"Level {new_level} ({xp_into}/{xp_need})"
                            )
                            if xp_res.streak_multiplier > 1.0:
                                xp_lines.append(
                                    f"🔥 Hard-exercise streak ×{xp_res.streak_multiplier:.1f} XP bonus!"
                                )
                            if new_level > old_level:
                                xp_lines.append(
                                    f"🎉 *LEVEL UP!* {skill.name} is now *Level {new_level}*"
                                )
    except Exception as e:
        logger.error(f"Failed to save/check test results: {e}")

    # Progressive difficulty suggestion (#3)
    count = state.get("count", len(pairs))
    progression_text = get_progression_suggestion(difficulty, count, score_pct)

    results_text = exercise.format_test_results(
        pairs, merged_results, difficulty,
        personal_best_text=personal_best_text,
        progression_text=progression_text,
        streak_text=streak_text,
    )

    if new_achievements:
        results_text += "\n\n🏅 *Achievement unlocked!*"
        for a in new_achievements:
            results_text += f"\n{a.emoji} *{a.name}* — {a.description}"

    if xp_lines:
        results_text += "\n\n" + "\n".join(xp_lines)

    state["test_active"] = False
    state["last_test_results"] = merged_results
    state["last_test_pairs"] = pairs

    # Save used words to rolling recent-words window (avoids repetition)
    try:
        await _save_recent_words(chat_id, pairs)
    except Exception as e:
        logger.error(f"Failed to save recent words: {e}")

    has_mistakes = any(not r["correct"] for r in merged_results)
    await context.bot.send_message(
        chat_id=chat_id, text=results_text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=exercise.get_results_keyboard(has_mistakes=has_mistakes),
    )


# ============================================================================
# Retry Mistakes (#6)
# ============================================================================

async def _start_retry_mistakes(query, context) -> None:
    state = get_user_state(context)
    last_results = state.get("last_test_results", [])
    last_pairs = state.get("last_test_pairs", [])
    difficulty = state.get("test_difficulty", state.get("difficulty", Difficulty.BEGINNER))

    wrong_indices = [r["pair_index"] for r in last_results if not r["correct"]]
    if not wrong_indices:
        await query.edit_message_text("No mistakes to retry! 🎉")
        return

    quiz_items = []
    random.shuffle(wrong_indices)
    for idx in wrong_indices:
        w1, w2 = last_pairs[idx]
        if random.choice([True, False]):
            shown, expected = w1, w2
        else:
            shown, expected = w2, w1
        quiz_items.append({"pair_index": idx, "shown_word": shown, "expected": expected})

    correct_results = [r for r in last_results if r["correct"]]

    set_user_state(context, "test_active", True)
    set_user_state(context, "test_pairs", last_pairs)
    set_user_state(context, "test_quiz_items", quiz_items)
    set_user_state(context, "test_current_index", 0)
    set_user_state(context, "test_results", [])
    set_user_state(context, "test_difficulty", difficulty)
    set_user_state(context, "test_chat_id", query.message.chat_id)
    set_user_state(context, "baseline_results", correct_results)

    n = len(quiz_items)
    await query.edit_message_text(
        f"🔁 *Retrying {n} mistake{'s' if n != 1 else ''}...*\n\n"
        f"Each question has *{QUESTION_TIME_LIMIT}s* to answer.",
        parse_mode=ParseMode.MARKDOWN,
    )
    _track_bot_message(state, query.message.message_id)
    await asyncio.sleep(1.0)
    await _send_next_question(
        context, query.message.chat_id, get_user_state(context), query.from_user.id,
    )


# ============================================================================
# Reverse Quiz (#1)
# ============================================================================

async def _start_reverse_quiz(query, context) -> None:
    """Re-quiz all pairs but with the shown/expected columns flipped."""
    state = get_user_state(context)
    last_pairs = state.get("last_test_pairs", [])
    last_results = state.get("last_test_results", [])
    difficulty = state.get("test_difficulty", state.get("difficulty", Difficulty.BEGINNER))

    if not last_pairs:
        await query.edit_message_text("No pairs available. Start a new test first.")
        return

    # Build reverse quiz: for each pair, flip which word is shown vs asked.
    # We look at the original results to find what was shown, then show the
    # opposite word this time.
    result_by_pair = {r["pair_index"]: r for r in last_results}
    quiz_order = list(range(len(last_pairs)))
    random.shuffle(quiz_order)

    quiz_items = []
    for idx in quiz_order:
        w1, w2 = last_pairs[idx]
        prev = result_by_pair.get(idx)
        if prev:
            # Show whichever word was the *answer* last time
            quiz_items.append({
                "pair_index": idx,
                "shown_word": prev["expected"],
                "expected": prev["shown_word"],
            })
        else:
            # No previous result — just flip randomly
            if random.choice([True, False]):
                quiz_items.append({"pair_index": idx, "shown_word": w2, "expected": w1})
            else:
                quiz_items.append({"pair_index": idx, "shown_word": w1, "expected": w2})

    set_user_state(context, "test_active", True)
    set_user_state(context, "test_pairs", last_pairs)
    set_user_state(context, "test_quiz_items", quiz_items)
    set_user_state(context, "test_current_index", 0)
    set_user_state(context, "test_results", [])
    set_user_state(context, "test_difficulty", difficulty)
    set_user_state(context, "test_chat_id", query.message.chat_id)
    set_user_state(context, "baseline_results", [])

    await query.edit_message_text(
        f"🔀 *Reverse Quiz — {len(quiz_items)} pairs*\n\n"
        "This time the columns are flipped!\n"
        f"Each question has *{QUESTION_TIME_LIMIT}s* to answer.",
        parse_mode=ParseMode.MARKDOWN,
    )
    _track_bot_message(state, query.message.message_id)
    await asyncio.sleep(1.0)
    await _send_next_question(
        context, query.message.chat_id, get_user_state(context), query.from_user.id,
    )


# ============================================================================
# Message Handler
# ============================================================================

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    if is_in_test_mode(context):
        try:
            await update.message.delete()
        except Exception:
            pass
        await _record_answer(
            context, update.message.chat_id, update.message.text,
            user_id=update.effective_user.id,
            answer_message_id=None,
        )
    else:
        await update.message.reply_text(
            "Use /start to see the main menu, or /help for instructions."
        )


# ============================================================================
# Keyboard Helpers
# ============================================================================

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for info in ExerciseRegistry.list_exercises():
        buttons.append([InlineKeyboardButton(
            f"🧠 {info['name']}", callback_data=f"exercise:{info['type']}",
        )])
    buttons.append([
        InlineKeyboardButton("🏅 Achievements", callback_data="menu:achievements"),
        InlineKeyboardButton("🏆 Leaderboard", callback_data="menu:leaderboard"),
    ])
    return InlineKeyboardMarkup(buttons)


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
        lines = [f"🏅 *Achievements — {len(unlocked)}/{len(ACHIEVEMENTS)}*\n"]
        for a in ACHIEVEMENTS:
            if a.code in unlocked:
                lines.append(f"{a.emoji} *{a.name}* — {a.description}  ✅")
            else:
                lines.append(f"🔒 {a.name} — {a.description}")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]])
        await query.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=kb,
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


# Callback routing table: prefix of callback_data -> handler(query, context, data).
# To add a new exercise flow, register its prefix here (usually the
# exercise_type string used in its keyboards).
CALLBACK_ROUTES = {
    "word_memo": handle_word_memo_callback,
    "lb": handle_leaderboard_callback,
    "menu": handle_menu_callback,
}
