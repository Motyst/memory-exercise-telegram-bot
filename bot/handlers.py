"""
Telegram Bot Handlers.
Handles all user interactions, commands, and callbacks.
"""

import asyncio
import logging
import random
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database import get_session, UserRepository, ExerciseSessionRepository, ExerciseType
from exercises import ExerciseRegistry, Difficulty
from exercises.word_memorization import (
    SECONDS_PER_PAIR,
    SPEED_MODE_MULTIPLIER,
    QUESTION_TIME_LIMIT,
    DIFFICULTY_NAMES,
    is_fuzzy_match,
    get_progression_suggestion,
)

logger = logging.getLogger(__name__)


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
    help_text = (
        "🆘 *Mental Training Bot Help*\n\n"
        "*Commands:*\n"
        "/start - Main menu\n"
        "/help - This help message\n"
        "/stats - Your training statistics\n"
        "/history - Last 10 test results\n"
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
    text = f"📊 *Your Training Statistics*\n\n{streak_line}\n*Total Sessions:* {stats['total_sessions']}\n"
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
    elif data.startswith("word_memo:"):
        await handle_word_memo_callback(query, context, data)
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

def _build_quiz_items(pairs, reverse=False):
    """Build shuffled quiz items. If reverse=True, flip shown/expected."""
    quiz_order = list(range(len(pairs)))
    random.shuffle(quiz_order)
    quiz_items = []
    for idx in quiz_order:
        w1, w2 = pairs[idx]
        if reverse:
            # Flip: if normally we'd show w1 and ask w2, now show w2 and ask w1
            if random.choice([True, False]):
                shown, expected = w2, w1
            else:
                shown, expected = w1, w2
            # Then flip the assignment
            shown, expected = expected, shown
        else:
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
    if state.get("test_active"):
        await _record_answer(context, chat_id, "(timed out)", user_id=user_id)


# ---- Core quiz flow ----

async def _send_next_question(context, chat_id, state, user_id=None) -> None:
    exercise = ExerciseRegistry.get("word_memo")
    current_index = state.get("test_current_index", 0)
    quiz_items = state.get("test_quiz_items", [])

    if current_index >= len(quiz_items):
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
        data={"user_id": user_id}, name=_question_timer_name(user_id),
    )


async def _record_answer(context, chat_id, answer_text, user_id=None, answer_message_id=None) -> None:
    if user_id is None:
        user_id = chat_id
    user_data = context.application.user_data.get(user_id, {})
    state = user_data.get("state", {})
    if not state.get("test_active"):
        return

    quiz_items = state.get("test_quiz_items", [])
    current_index = state.get("test_current_index", 0)
    results = state.get("test_results", [])
    if current_index >= len(quiz_items):
        return
    item = quiz_items[current_index]

    _cancel_question_timer(context, user_id)

    if answer_message_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=answer_message_id)
        except Exception:
            pass

    is_special = answer_text in ("(skipped)", "(timed out)")
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
    exercise = ExerciseRegistry.get("word_memo")
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

    # Personal best check + streak update
    personal_best_text = None
    streak_text = None
    try:
        async with get_session() as session:
            user_repo = UserRepository(session)
            session_repo = ExerciseSessionRepository(session)
            db_user = await user_repo.get_by_telegram_id(chat_id)
            if db_user:
                difficulty_value = difficulty.value if difficulty else "sr_review"
                prev_best = await session_repo.get_personal_best(
                    db_user.id, difficulty_value, len(pairs),
                ) if difficulty else None
                # Save current session
                await session_repo.create(
                    user_id=db_user.id,
                    exercise_type=ExerciseType.WORD_MEMORIZATION,
                    difficulty=difficulty_value,
                    parameters={
                        "count": len(pairs), "mode": "test",
                        "score": correct_count, "max_score": total,
                    },
                )
                # Personal best
                if difficulty:
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
        await _record_answer(
            context, update.message.chat_id, update.message.text,
            user_id=update.effective_user.id,
            answer_message_id=update.message.message_id,
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
    return InlineKeyboardMarkup(buttons)
