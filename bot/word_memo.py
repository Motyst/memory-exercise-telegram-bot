"""
Word Memorization flow: menu callbacks (format → mode → difficulty → speed →
count), training/test generation, and the placement test. Quiz mechanics
(timers, answers, results) live in quiz_engine.py.
"""

import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from database import (
    get_session, UserRepository, ExerciseSessionRepository, ExerciseType,
)
from exercises import ExerciseRegistry, Difficulty
from exercises.word_memorization import (
    SECONDS_PER_PAIR,
    SECONDS_PER_WORD,
    SPEED_MODE_MULTIPLIER,
    QUESTION_TIME_LIMIT,
    DIFFICULTY_NAMES,
    FORMAT_UNITS,
    NEXT_COUNT,
)
from .analytics import mark_round_start
from .quiz_engine import (
    cancel_question_timer, record_answer, start_quiz_after_timer,
    start_retry_mistakes, start_reverse_quiz,
)
from .recent_words import get_recent_words, save_recent_words
from .state import (
    get_user_state, set_user_state, clear_user_state,
    track_bot_message, cleanup_bot_messages,
)


async def handle_word_memo_callback(query, context, data: str) -> None:
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else None
    value = parts[2] if len(parts) > 2 else None
    exercise = ExerciseRegistry.get("word_memo")

    if action == "start":
        cancel_question_timer(context, query.from_user.id)
        state = get_user_state(context)
        await cleanup_bot_messages(context.bot, query.message.chat_id, state)
        clear_user_state(context)
        set_user_state(context, "current_exercise", "word_memo")
        kb = exercise.get_mode_keyboard()
        await query.edit_message_text(
            exercise.get_intro_message(), parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )

    elif action == "format":
        # First step: what to memorize — "pairs" or "list"
        set_user_state(context, "format", value)
        await query.edit_message_text(
            exercise.get_mode_message(value), parse_mode=ParseMode.MARKDOWN,
            reply_markup=exercise.get_mode_select_keyboard(),
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
        fmt = state.get("format", "pairs")

        if mode == "test":
            # Show speed selection for test mode
            await query.edit_message_text(
                exercise.get_speed_message(difficulty, fmt),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=exercise.get_speed_keyboard(),
            )
        else:
            # Training mode goes straight to count
            mode_label = "📝 Training"
            diff_label = DIFFICULTY_NAMES[difficulty]
            unit = FORMAT_UNITS.get(fmt, "pairs")
            await query.edit_message_text(
                f"*Mode:* {mode_label}\n*Difficulty:* {diff_label}\n\nHow many {unit}?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=exercise.get_parameter_keyboard_training(difficulty, fmt),
            )

    elif action == "speed":
        # Speed selection: value is "normal" or "fast"
        set_user_state(context, "speed_mode", value == "fast")
        state = get_user_state(context)
        difficulty = state.get("difficulty", Difficulty.BEGINNER)
        fmt = state.get("format", "pairs")
        speed_label = "⚡ Speed" if value == "fast" else "🕐 Normal"
        diff_label = DIFFICULTY_NAMES[difficulty]
        unit = FORMAT_UNITS.get(fmt, "pairs")
        await query.edit_message_text(
            f"*Mode:* 🎯 Test\n*Difficulty:* {diff_label}\n*Pace:* {speed_label}\n\nHow many {unit}?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=exercise.get_parameter_keyboard(difficulty, fmt),
        )

    elif action == "back_to_mode":
        state = get_user_state(context)
        fmt = state.get("format", "pairs")
        await query.edit_message_text(
            exercise.get_mode_message(fmt), parse_mode=ParseMode.MARKDOWN,
            reply_markup=exercise.get_mode_select_keyboard(),
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
        fmt = state.get("format", "pairs")
        await query.edit_message_text(
            exercise.get_speed_message(difficulty, fmt), parse_mode=ParseMode.MARKDOWN,
            reply_markup=exercise.get_speed_keyboard(),
        )

    elif action in ("count", "next_count"):
        # "count" = picked from the keyboard; "next_count" = Level Up button
        # on a results/completion screen — same flow, new size, same settings.
        count = int(value)
        set_user_state(context, "count", count)
        state = get_user_state(context)
        difficulty = state.get("difficulty", Difficulty.BEGINNER)
        mode = state.get("mode", "training")
        if action == "next_count":
            await cleanup_bot_messages(context.bot, query.message.chat_id, state)
        if mode == "test":
            await generate_word_memo_test(query, context, difficulty, count)
        else:
            await generate_word_memo(query, context, difficulty, count)

    elif action == "speed_run":
        # ⚡ Speed run (progression ladder): rerun the same test with speed
        # mode on. Button only appears on test results, so mode is test.
        set_user_state(context, "speed_mode", True)
        state = get_user_state(context)
        difficulty = state.get("difficulty", Difficulty.BEGINNER)
        count = state.get("count", 10)
        await cleanup_bot_messages(context.bot, query.message.chat_id, state)
        await generate_word_memo_test(query, context, difficulty, count)

    elif action == "again":
        state = get_user_state(context)
        difficulty = state.get("difficulty", Difficulty.BEGINNER)
        count = state.get("count", 10)
        mode = state.get("mode", "training")
        await cleanup_bot_messages(context.bot, query.message.chat_id, state)
        if mode == "test":
            await generate_word_memo_test(query, context, difficulty, count)
        else:
            await generate_word_memo(query, context, difficulty, count)

    elif action == "settings":
        cancel_question_timer(context, query.from_user.id)
        state = get_user_state(context)
        await cleanup_bot_messages(context.bot, query.message.chat_id, state)
        clear_user_state(context)
        set_user_state(context, "current_exercise", "word_memo")
        kb = exercise.get_mode_keyboard()
        await query.edit_message_text(
            exercise.get_intro_message(), parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )

    elif action == "skip":
        cancel_question_timer(context, query.from_user.id)
        await record_answer(context, query.message.chat_id, "(skipped)")

    elif action == "retry_mistakes":
        await start_retry_mistakes(query, context)

    elif action == "reverse_quiz":
        await start_reverse_quiz(query, context)


# ============================================================================
# Training Mode
# ============================================================================

async def generate_word_memo(query, context, difficulty, count) -> None:
    user = query.from_user
    exercise = ExerciseRegistry.get("word_memo")
    state = get_user_state(context)
    fmt = state.get("format", "pairs")
    recent = await get_recent_words(user.id)
    async with get_session() as session:
        user_repo = UserRepository(session)
        session_repo = ExerciseSessionRepository(session)
        db_user = await user_repo.get_by_telegram_id(user.id)
        if db_user:
            await session_repo.create(
                user_id=db_user.id, exercise_type=ExerciseType.WORD_MEMORIZATION,
                difficulty=difficulty.value,
                parameters={"count": count, "mode": "training", "format": fmt},
            )
    result = await exercise.generate(
        difficulty=difficulty,
        parameters={"count": count, "recent_words": recent, "format": fmt},
    )
    items = result.additional_data["words" if fmt == "list" else "pairs"]
    await save_recent_words(user.id, items)
    await query.edit_message_text(
        result.text_content, parse_mode=ParseMode.MARKDOWN,
        reply_markup=exercise.get_completion_keyboard(
            next_count=NEXT_COUNT.get(count), fmt=fmt,
        ),
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


def _build_list_quiz_items(words):
    """Build sequential chain quiz items over an ordered word list.

    Question i shows words[i] and expects words[i+1], walked in list order so
    every word is tested exactly once as an answer. Each prompt doubles as the
    reveal of the previous answer (skip or wrong — the next question shows it).
    pair_index stores the link index so retry/results machinery works unchanged.
    """
    return [
        {"pair_index": i, "direction": "next",
         "shown_word": words[i], "expected": words[i + 1]}
        for i in range(len(words) - 1)
    ]


async def generate_word_memo_test(query, context, difficulty, count, round_mode: str = "test") -> None:
    exercise = ExerciseRegistry.get("word_memo")
    state = get_user_state(context)
    fmt = state.get("format", "pairs")
    recent = await get_recent_words(query.from_user.id)
    result = await exercise.generate(
        difficulty=difficulty,
        parameters={"count": count, "recent_words": recent, "format": fmt},
    )

    speed = state.get("speed_mode", False)
    multiplier = SPEED_MODE_MULTIPLIER if speed else 1.0

    if fmt == "list":
        items = result.additional_data["words"]
        countdown_seconds = int(count * SECONDS_PER_WORD * multiplier)
        quiz_items = _build_list_quiz_items(items)
        study_text = exercise.format_list_text_for_test(
            items, difficulty, countdown_seconds, speed_mode=speed,
        )
    else:
        items = result.additional_data["pairs"]
        countdown_seconds = int(count * SECONDS_PER_PAIR * multiplier)
        quiz_items = _build_quiz_items(items)
        study_text = exercise.format_pairs_text_for_test(
            items, difficulty, countdown_seconds, speed_mode=speed,
        )

    set_user_state(context, "test_active", False)
    set_user_state(context, "test_exercise_type", exercise.exercise_type)
    set_user_state(context, "test_pairs", items)
    set_user_state(context, "test_format", fmt)
    set_user_state(context, "test_quiz_items", quiz_items)
    set_user_state(context, "test_current_index", 0)
    set_user_state(context, "test_results", [])
    set_user_state(context, "test_difficulty", difficulty)
    set_user_state(context, "test_chat_id", query.message.chat_id)
    set_user_state(context, "baseline_results", [])
    set_user_state(context, "test_round_mode", round_mode)
    # Fresh study phase — reset the repeat-round counters that throttle XP
    # on reverse/retry chains (see quiz_engine._show_test_results).
    set_user_state(context, "test_reverse_rounds", 0)
    set_user_state(context, "test_retry_rounds", 0)
    # Training-time clock starts with the study phase — memorizing IS the work.
    mark_round_start(state)
    await query.edit_message_text(study_text, parse_mode=ParseMode.MARKDOWN)

    set_user_state(context, "test_study_message_id", query.message.message_id)
    track_bot_message(state, query.message.message_id)

    context.job_queue.run_once(
        start_quiz_after_timer, when=countdown_seconds,
        chat_id=query.message.chat_id, user_id=query.from_user.id,
        data={"user_id": query.from_user.id},
        name=f"quiz_timer_{query.from_user.id}",
    )


# ============================================================================
# Placement Test (level calibration for new users)
# ============================================================================

# One fixed round at the middle difficulty gives the most signal per minute:
# a beginner bombs it, an expert aces it, everyone else lands in between.
PLACEMENT_DIFFICULTY = Difficulty.INTERMEDIATE
PLACEMENT_COUNT = 10


async def handle_placement_callback(query, context, data: str) -> None:
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else None

    if action == "start":
        # Explainer screen — the study list appearing without warning would
        # waste the (unannounced) memorization countdown.
        cancel_question_timer(context, query.from_user.id)
        state = get_user_state(context)
        await cleanup_bot_messages(context.bot, query.message.chat_id, state)
        clear_user_state(context)
        await query.edit_message_text(
            "📏 *Level Test*\n\n"
            f"One short test to find your starting level:\n"
            f"• {PLACEMENT_COUNT} word pairs to memorize\n"
            f"• Then a quiz on each pair ({QUESTION_TIME_LIMIT}s per question)\n"
            f"• Takes about 2 minutes\n\n"
            "Your score won't affect stats or the leaderboard — "
            "it just picks the right level for you.\n\n"
            "The word list appears as soon as you press Begin. Ready?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Begin", callback_data="placement:begin")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
            ]),
        )

    elif action == "begin":
        set_user_state(context, "current_exercise", "word_memo")
        set_user_state(context, "mode", "test")
        set_user_state(context, "format", "pairs")
        set_user_state(context, "speed_mode", False)
        set_user_state(context, "difficulty", PLACEMENT_DIFFICULTY)
        set_user_state(context, "count", PLACEMENT_COUNT)
        await generate_word_memo_test(
            query, context, PLACEMENT_DIFFICULTY, PLACEMENT_COUNT,
            round_mode="placement",
        )

    elif action == "apply":
        # placement:apply:<difficulty>:<count> — start a real test with the
        # recommended settings in one tap.
        difficulty = Difficulty(parts[2])
        count = int(parts[3])
        state = get_user_state(context)
        await cleanup_bot_messages(context.bot, query.message.chat_id, state)
        set_user_state(context, "current_exercise", "word_memo")
        set_user_state(context, "mode", "test")
        set_user_state(context, "format", "pairs")
        set_user_state(context, "speed_mode", False)
        set_user_state(context, "difficulty", difficulty)
        set_user_state(context, "count", count)
        await generate_word_memo_test(query, context, difficulty, count)
