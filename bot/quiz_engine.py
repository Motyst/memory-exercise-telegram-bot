"""
Shared quiz engine: per-question timers, the answer-grace window, answer
recording, the results pipeline (save/PB/streak/achievements/XP), and the
retry-mistakes / reverse-quiz rounds.

Any exercise can reuse the engine by setting the test_* state keys plus
test_exercise_type, providing format_test_prompt / get_skip_keyboard /
format_test_results / get_results_keyboard on the exercise class, and adding
its registry key to ENGINE_EXERCISE_ENUM below.

Word-memo remnants: progression suggestions and the placement branch in
_show_test_results still call word-memo helpers directly. When a second
engine-based exercise lands, lift these into exercise hooks.
"""

import asyncio
import logging
import random
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from database import (
    get_session, UserRepository, ExerciseSessionRepository,
    AchievementRepository, UserSkillRepository, ExerciseType,
)
from database.models import utcnow
from exercises import ExerciseRegistry, Difficulty
from gamification import (
    AchievementContext, evaluate_achievements,
    SKILLS, EXERCISE_SKILLS, compute_test_xp, level_from_xp,
)
from exercises.word_memorization import (
    QUESTION_TIME_LIMIT,
    DIFFICULTY_NAMES,
    DIFF_EMOJI,
    NEXT_COUNT,
    is_fuzzy_match,
    get_progression_suggestion,
    get_placement_recommendation,
    should_offer_speed_run,
)
from .features import is_xp_enabled
from .recent_words import save_recent_words
from .state import (
    get_answer_lock, get_job_user_state, get_user_state,
    set_user_state, track_bot_message, cleanup_bot_messages,
)

logger = logging.getLogger(__name__)

# If a text answer arrives within this many seconds after a question timed out,
# it was almost certainly typed for the timed-out question (the next question
# hasn't visibly rendered yet) — apply it retroactively instead of counting it
# against the new question.
ANSWER_TIMEOUT_GRACE = 2.0

# DB enum for each registry key that runs tests through this engine.
# New engine-based exercises: add their mapping here.
ENGINE_EXERCISE_ENUM = {
    "word_memo": ExerciseType.WORD_MEMORIZATION,
}


def _exercise_enum(state: dict) -> ExerciseType:
    return ENGINE_EXERCISE_ENUM[state.get("test_exercise_type", "word_memo")]


# ============================================================================
# Per-question timer
# ============================================================================

def _question_timer_name(user_id: int) -> str:
    return f"question_timer_{user_id}"


def cancel_question_timer(context, user_id: int) -> None:
    for job in context.job_queue.get_jobs_by_name(_question_timer_name(user_id)):
        job.schedule_removal()


async def _question_timeout_callback(context) -> None:
    job = context.job
    chat_id, user_id = job.chat_id, job.data["user_id"]
    state = get_job_user_state(context.application, user_id)
    if not state.get("test_active"):
        return
    # Stale-timer guard: if the index already moved past the question this
    # timer was armed for, the user answered in time — don't time out the
    # next question.
    if state.get("test_current_index", 0) != job.data.get("question_index"):
        return
    state["last_timeout_at"] = time.monotonic()
    await record_answer(context, chat_id, "(timed out)", user_id=user_id)


# ============================================================================
# Study-phase countdown → quiz start
# ============================================================================

async def start_quiz_after_timer(context) -> None:
    job = context.job
    chat_id, user_id = job.chat_id, job.data["user_id"]
    state = get_job_user_state(context.application, user_id)
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
    await send_next_question(context, chat_id, state, user_id)


# ============================================================================
# Core quiz flow
# ============================================================================

async def send_next_question(context, chat_id, state, user_id=None) -> None:
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
        await cleanup_bot_messages(context.bot, chat_id, state)
        await _show_test_results(context, chat_id, state)
        return

    item = quiz_items[current_index]
    prompt = exercise.format_test_prompt(
        item["shown_word"], current_index + 1, len(quiz_items),
        direction=item.get("direction"),
    )
    msg = await context.bot.send_message(
        chat_id=chat_id, text=prompt, parse_mode=ParseMode.MARKDOWN,
        reply_markup=exercise.get_skip_keyboard(QUESTION_TIME_LIMIT),
    )
    state["test_prompt_message_id"] = msg.message_id
    track_bot_message(state, msg.message_id)

    if user_id is None:
        user_id = chat_id
    cancel_question_timer(context, user_id)
    context.job_queue.run_once(
        _question_timeout_callback, when=QUESTION_TIME_LIMIT,
        chat_id=chat_id, user_id=user_id,
        data={"user_id": user_id, "question_index": current_index},
        name=_question_timer_name(user_id),
    )


async def record_answer(context, chat_id, answer_text, user_id=None, answer_message_id=None) -> None:
    if user_id is None:
        user_id = chat_id
    async with get_answer_lock(user_id):
        await _record_answer_impl(context, chat_id, answer_text, user_id, answer_message_id)


async def _record_answer_impl(context, chat_id, answer_text, user_id, answer_message_id) -> None:
    state = get_job_user_state(context.application, user_id)

    results = state.get("test_results", [])
    is_special = answer_text in ("(skipped)", "(timed out)")

    # Late-answer grace: the previous question just timed out and this text
    # arrived moments later — the user typed it for the timed-out question,
    # not the one that hasn't visibly appeared yet. Re-score the previous
    # result and leave the current question (and its timer) untouched.
    # Runs BEFORE the active/index guards so it also covers the final
    # question, whose results render immediately after timeout.
    # Disabled for list format: the sequential chain means the next prompt
    # displays the timed-out question's answer, so a grace credit would let
    # the user copy it off the screen.
    last_timeout_at = state.get("last_timeout_at")
    if (
        not is_special
        and state.get("test_format") != "list"
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

    cancel_question_timer(context, user_id)

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
        "direction": item.get("direction"),
    })
    state["test_results"] = results
    state["test_current_index"] = current_index + 1
    await send_next_question(context, chat_id, state, user_id)


# ============================================================================
# Results pipeline
# ============================================================================

async def _show_test_results(context, chat_id, state) -> None:
    state["last_timeout_at"] = None
    exercise_key = state.get("test_exercise_type", "word_memo")
    exercise = ExerciseRegistry.get(exercise_key)
    exercise_enum = _exercise_enum(state)
    pairs = state.get("test_pairs", [])
    results = state.get("test_results", [])
    difficulty = state.get("test_difficulty", Difficulty.BEGINNER)
    baseline = state.get("baseline_results", [])
    fmt = state.get("test_format", "pairs")

    # Merge baseline + retry results
    merged_by_pair = {r["pair_index"]: r for r in baseline}
    for r in results:
        merged_by_pair[r["pair_index"]] = r
    merged_results = list(merged_by_pair.values())

    correct_count = sum(1 for r in merged_results if r["correct"])
    # List format: N words yield N-1 adjacent-link questions
    total = max(len(pairs) - 1, 1) if fmt == "list" else len(pairs)
    score_pct = (correct_count / total * 100) if total > 0 else 0

    # "test" (fresh) | "reverse" | "reverse_extra" (2nd+ reverse on the same
    # set) | "retry" | "placement" — stored in session parameters so
    # stats/leaderboard can exclude retry, placement and extra-reverse rounds
    # (practice/calibration, not real tests).
    round_mode = state.get("test_round_mode", "test")
    is_retry = round_mode == "retry"
    is_placement = round_mode == "placement"
    is_extra_reverse = round_mode == "reverse_extra"

    # XP throttle for repeat rounds on one memorized set: first reverse earns
    # half (recalling backwards is real work, but the set was already paid out
    # in full), everything after that — extra reverses, second+ retries —
    # earns nothing. Blocks the farm loop: memorize once, reverse forever.
    if round_mode == "reverse":
        xp_mult = 0.5
    elif is_extra_reverse or (is_retry and state.get("test_retry_rounds", 1) > 1):
        xp_mult = 0.0
    else:
        xp_mult = 1.0

    # Personal best check + streak update + achievements + XP
    personal_best_text = None
    streak_text = None
    new_achievements = []
    xp_lines = []
    compact = False
    try:
        async with get_session() as session:
            user_repo = UserRepository(session)
            session_repo = ExerciseSessionRepository(session)
            achievement_repo = AchievementRepository(session)
            db_user = await user_repo.get_by_telegram_id(chat_id)
            if db_user:
                compact = (db_user.preferences or {}).get("compact_results", False)
                difficulty_value = difficulty.value
                prev_best = await session_repo.get_personal_best(
                    db_user.id, difficulty_value, len(pairs), fmt=fmt,
                )
                # Save current session — score/max_score go into real columns
                # so stats, leaderboard and admin views aggregate in SQL.
                await session_repo.create(
                    user_id=db_user.id,
                    exercise_type=exercise_enum,
                    difficulty=difficulty_value,
                    parameters={
                        "count": len(pairs), "mode": round_mode, "format": fmt,
                        "speed": state.get("speed_mode", False),
                    },
                    score=correct_count, max_score=total, completed=True,
                )
                # Personal best — not on retries (merged score mixes baseline
                # with a redo), not on placement (one-off calibration), not on
                # extra reverses (unscored practice rounds).
                if not is_retry and not is_placement and not is_extra_reverse:
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
                # Achievements — skipped on retries so a retried-to-100% score
                # can't farm perfect-score achievements; skipped on placement
                # and extra reverses (excluded from scored tests entirely).
                if not is_retry and not is_placement and not is_extra_reverse:
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
                # Placement: store the recommendation so it survives restarts
                # and can pre-star the difficulty keyboard later.
                if is_placement:
                    rec_diff, rec_count = get_placement_recommendation(score_pct)
                    await user_repo.update_preferences(chat_id, {
                        "placement": {
                            "level": rec_diff.value,
                            "count": rec_count,
                            "score": round(score_pct),
                            "date": utcnow().isoformat(),
                        }
                    })
                # XP — based on THIS round's questions (a fresh test = full
                # set; retry-mistakes = just the retried subset), scaled by
                # the repeat-round throttle above (first reverse ×0.5,
                # anything after that 0 — one memorized set pays out once).
                # None for placement — calibration, not grind.
                if is_xp_enabled() and not is_placement:
                    skill_code = EXERCISE_SKILLS.get(exercise_enum.value)
                    quiz_items = state.get("test_quiz_items", [])
                    if skill_code and quiz_items and xp_mult == 0.0:
                        xp_lines.append(
                            "💡 _No XP — you've mastered this set. "
                            "Start a fresh test to keep earning!_"
                        )
                    elif skill_code and quiz_items:
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
                        xp_award = round(xp_res.xp * xp_mult)
                        # ⚡ Fresh-mind bonus: test launched from a daily
                        # reminder ping within the window. Remove this block
                        # together with bot/reminders.py (lazy import — a
                        # top-level one would be a circular import).
                        fresh_xp = 0
                        if (
                            xp_award > 0 and round_mode == "test"
                            and state.pop("fresh_mind_pending", None)
                        ):
                            from .reminders import claim_fresh_mind_bonus
                            fresh_xp = await claim_fresh_mind_bonus(
                                user_repo, db_user, xp_award
                            )
                        new_level, xp_into, xp_need = level_from_xp(
                            skill_row.xp + xp_award + fresh_xp
                        )
                        await skill_repo.add_xp(
                            db_user.id, skill_code, xp_award + fresh_xp,
                            new_level, xp_res.new_hard_streak,
                        )
                        if xp_award > 0:
                            skill = SKILLS[skill_code]
                            xp_lines.append(
                                f"⭐ *+{xp_award} XP* {skill.emoji} {skill.name} — "
                                f"Level {new_level} ({xp_into}/{xp_need})"
                            )
                            if xp_mult < 1.0 and not compact:
                                xp_lines.append(
                                    "🔀 Reverse round — half XP (fresh tests pay full)"
                                )
                            if fresh_xp:
                                xp_lines.append(
                                    f"⚡ Fresh-mind bonus — +{fresh_xp} XP for "
                                    "answering the reminder fast!"
                                )
                            if xp_res.streak_multiplier > 1.0 and not compact:
                                xp_lines.append(
                                    f"🔥 Hard-exercise streak ×{xp_res.streak_multiplier:.1f} XP bonus!"
                                )
                            if new_level > old_level:
                                xp_lines.append(
                                    f"🎉 *LEVEL UP!* {skill.name} is now *Level {new_level}*"
                                )
    except Exception as e:
        logger.error(f"Failed to save/check test results: {e}")

    # Progressive difficulty suggestion — not on placement, which makes
    # its own recommendation below.
    count = state.get("count", len(pairs))
    speed_mode = state.get("speed_mode", False)
    progression_text = None
    if not is_placement:
        progression_text = get_progression_suggestion(
            difficulty, count, score_pct, fmt, speed_mode
        )

    results_text = exercise.format_test_results(
        pairs, merged_results, difficulty,
        personal_best_text=personal_best_text,
        progression_text=progression_text,
        streak_text=streak_text,
        compact=compact,
        fmt=fmt,
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

    # Save used words to rolling recent-words window (avoids repetition).
    # Word-memo only — other exercises' items aren't vocabulary.
    if exercise_key == "word_memo":
        try:
            await save_recent_words(chat_id, pairs)
        except Exception as e:
            logger.error(f"Failed to save recent words: {e}")

    if is_placement:
        rec_diff, rec_count = get_placement_recommendation(score_pct)
        rec_label = DIFFICULTY_NAMES[rec_diff]
        results_text += (
            f"\n\n📏 *Your level:* {DIFF_EMOJI[rec_diff.value]} *{rec_label}* · "
            f"*{rec_count} pairs*\n"
            "This test doesn't count toward your stats — "
            "your real training starts now!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"🚀 Start: {rec_label}, {rec_count} pairs",
                callback_data=f"placement:apply:{rec_diff.value}:{rec_count}",
            )],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ])
    else:
        has_mistakes = any(not r["correct"] for r in merged_results)
        kb_kwargs = dict(
            has_mistakes=has_mistakes,
            next_count=NEXT_COUNT.get(count),
            fmt=fmt,
        )
        # Progression-ladder speed button is word-memo only; other engine
        # exercises don't take the kwarg.
        if exercise_key == "word_memo":
            kb_kwargs["offer_speed_run"] = should_offer_speed_run(
                count, score_pct, speed_mode
            )
        keyboard = exercise.get_results_keyboard(**kb_kwargs)

    await context.bot.send_message(
        chat_id=chat_id, text=results_text, parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


# ============================================================================
# Retry Mistakes
# ============================================================================

async def start_retry_mistakes(query, context) -> None:
    state = get_user_state(context)
    last_results = state.get("last_test_results", [])
    last_pairs = state.get("last_test_pairs", [])
    difficulty = state.get("test_difficulty", state.get("difficulty", Difficulty.BEGINNER))

    wrong_results = [r for r in last_results if not r["correct"]]
    if not wrong_results:
        await query.edit_message_text("No mistakes to retry! 🎉")
        return

    fmt = state.get("test_format", "pairs")
    quiz_items = []
    if fmt == "list":
        # Re-ask the missed link questions (same direction), in chain order.
        # Prev-direction rounds run descending: ascending would leak — the
        # prompt for link i (words[i+1]) is the expected answer for link i+1.
        descending = any(r.get("direction") == "prev" for r in wrong_results)
        wrong_results.sort(key=lambda r: r["pair_index"], reverse=descending)
        for r in wrong_results:
            quiz_items.append({
                "pair_index": r["pair_index"], "direction": r.get("direction"),
                "shown_word": r["shown_word"], "expected": r["expected"],
            })
    else:
        random.shuffle(wrong_results)
        for r in wrong_results:
            idx = r["pair_index"]
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
    set_user_state(context, "test_round_mode", "retry")
    # Repeat-round throttle: only the first retry after a study phase earns
    # XP (see _show_test_results) — otherwise deliberate mistakes + endless
    # redo rounds would farm XP from a single memorized set.
    set_user_state(
        context, "test_retry_rounds", state.get("test_retry_rounds", 0) + 1
    )

    n = len(quiz_items)
    await query.edit_message_text(
        f"🔁 *Retrying {n} mistake{'s' if n != 1 else ''}...*\n\n"
        f"Each question has *{QUESTION_TIME_LIMIT}s* to answer.",
        parse_mode=ParseMode.MARKDOWN,
    )
    track_bot_message(state, query.message.message_id)
    await asyncio.sleep(1.0)
    await send_next_question(
        context, query.message.chat_id, get_user_state(context), query.from_user.id,
    )


# ============================================================================
# Reverse Quiz
# ============================================================================

async def start_reverse_quiz(query, context) -> None:
    """Re-quiz all pairs but with the shown/expected columns flipped."""
    state = get_user_state(context)
    last_pairs = state.get("last_test_pairs", [])
    last_results = state.get("last_test_results", [])
    difficulty = state.get("test_difficulty", state.get("difficulty", Difficulty.BEGINNER))

    if not last_pairs:
        await query.edit_message_text("No pairs available. Start a new test first.")
        return

    # Build reverse quiz. Pairs: for each question, flip which word is shown
    # vs asked (we look at the original results to find what was shown, then
    # show the opposite word this time). List: walk the chain backwards — show
    # a word, recall the one right before it, from the end of the list down.
    fmt = state.get("test_format", "pairs")
    result_by_pair = {r["pair_index"]: r for r in last_results}

    quiz_items = []
    if fmt == "list":
        for idx in range(len(last_pairs) - 2, -1, -1):
            quiz_items.append({
                "pair_index": idx, "direction": "prev",
                "shown_word": last_pairs[idx + 1], "expected": last_pairs[idx],
            })
    else:
        quiz_order = list(range(len(last_pairs)))
        random.shuffle(quiz_order)
        for idx in quiz_order:
            prev = result_by_pair.get(idx)
            w1, w2 = last_pairs[idx]
            if prev:
                # Show whichever word was the *answer* last time
                quiz_items.append({
                    "pair_index": idx,
                    "shown_word": prev["expected"],
                    "expected": prev["shown_word"],
                })
            elif random.choice([True, False]):
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
    # Repeat-round throttle: the first reverse after a study phase is a real
    # skill test (half XP, counts as a scored test); every further reverse on
    # the same memorized set is saved as "reverse_extra" — no XP, excluded
    # from stats/leaderboard/PB/achievements (see _show_test_results and
    # repositories._IS_SCORED_TEST) so it can't be farmed.
    reverse_rounds = state.get("test_reverse_rounds", 0) + 1
    set_user_state(context, "test_reverse_rounds", reverse_rounds)
    set_user_state(
        context, "test_round_mode",
        "reverse" if reverse_rounds == 1 else "reverse_extra",
    )

    flip_note = (
        "This time you walk the list *backwards* — recall the word that came before!" if fmt == "list"
        else "This time the columns are flipped!"
    )
    await query.edit_message_text(
        f"🔀 *Reverse Quiz — {len(quiz_items)} questions*\n\n"
        f"{flip_note}\n"
        f"Each question has *{QUESTION_TIME_LIMIT}s* to answer.",
        parse_mode=ParseMode.MARKDOWN,
    )
    track_bot_message(state, query.message.message_id)
    await asyncio.sleep(1.0)
    await send_next_question(
        context, query.message.chat_id, get_user_state(context), query.from_user.id,
    )
