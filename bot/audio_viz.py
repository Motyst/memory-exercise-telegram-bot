"""
Audio Visualization exercise flow.

Kept in its own module (one import + one CALLBACK_ROUTES entry in handlers.py)
so the whole exercise can be removed or paused without touching shared code.

Session bookkeeping:
- Passive listen  -> mode "audio_listen", no score, NO streak (passive work).
- Detail quiz     -> mode "audio_quiz", scored, streak counts. Excluded from
  stats/leaderboard/PB via _IS_SCORED_TEST (see database/repositories.py).
"""

import logging
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from database import (
    get_session, UserRepository, ExerciseSessionRepository, ExerciseType,
)
from exercises import ExerciseRegistry
from exercises.audio_visualization import (
    LENGTH_BUCKETS, HEARD_PREF_KEY,
    scan_library, pick_story, get_cached_file_id, save_file_id,
)
from .features import is_flag_enabled, AUDIO_VIZ_ENABLED_KEY, AUDIO_VIZ_QUIZ_ENABLED_KEY

logger = logging.getLogger(__name__)

# Keep at most this many story ids in the anti-repeat preference list.
HEARD_WINDOW = 100


def _state(context) -> dict:
    if "state" not in context.user_data:
        context.user_data["state"] = {}
    return context.user_data["state"]


async def _get_heard(telegram_id: int) -> list[str]:
    async with get_session() as session:
        db_user = await UserRepository(session).get_by_telegram_id(telegram_id)
        if not db_user:
            return []
        return (db_user.preferences or {}).get(HEARD_PREF_KEY, [])


async def _mark_heard(telegram_id: int, story_id: str) -> None:
    async with get_session() as session:
        user_repo = UserRepository(session)
        db_user = await user_repo.get_by_telegram_id(telegram_id)
        if not db_user:
            return
        heard = (db_user.preferences or {}).get(HEARD_PREF_KEY, [])
        heard = ([s for s in heard if s != story_id] + [story_id])[-HEARD_WINDOW:]
        await user_repo.update_preferences(telegram_id, {HEARD_PREF_KEY: heard})


async def _save_session(
    telegram_id: int, story_id: str, bucket: str,
    quiz_taken: bool, score: int | None = None, max_score: int | None = None,
) -> None:
    async with get_session() as session:
        db_user = await UserRepository(session).get_by_telegram_id(telegram_id)
        if not db_user:
            return
        await ExerciseSessionRepository(session).create(
            user_id=db_user.id,
            exercise_type=ExerciseType.AUDIO_VISUALIZATION,
            difficulty=bucket,
            parameters={
                "mode": "audio_quiz" if quiz_taken else "audio_listen",
                "story": story_id,
                "quiz_taken": quiz_taken,
            },
            score=score, max_score=max_score, completed=True,
        )


async def _delete_audio_message(context, chat_id: int, state: dict) -> None:
    """Delete the story's audio message from the chat.

    Telegram's music player queues every audio file in a chat as a playlist,
    so a finished story auto-plays into an older one still in the chat. Keeping
    at most one story audio around (delete on finish + before sending the next)
    kills the autoplay; the quiz path also relies on this as anti-cheat.
    """
    audio_msg_id = state.pop("audio_msg_id", None)
    if audio_msg_id:
        try:
            await context.bot.delete_message(
                chat_id=chat_id, message_id=audio_msg_id,
            )
        except Exception:
            pass


async def handle_audio_viz_callback(query, context, data: str) -> None:
    if not is_flag_enabled(AUDIO_VIZ_ENABLED_KEY):
        await query.edit_message_text(
            "🎧 This exercise is paused right now. Check back soon!",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
            ),
        )
        return

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else None
    exercise = ExerciseRegistry.get("audio_viz")

    if action == "start":
        library = scan_library()
        if not library:
            await query.edit_message_text(
                "🎧 No stories available yet — check back soon!",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
                ),
            )
            return
        state = _state(context)
        state.pop("audio_quiz_items", None)
        await query.edit_message_text(
            exercise.get_intro_message(), parse_mode=ParseMode.MARKDOWN,
            reply_markup=exercise.get_length_keyboard(list(library.keys())),
        )

    elif action == "len":
        await _start_story(query, context, exercise, bucket=parts[2])

    elif action == "done":
        await _finish_passive(query, context, exercise)

    elif action == "quiz":
        await _start_quiz(query, context)

    elif action == "ans":
        await _record_quiz_answer(query, context, exercise, parts)


async def _start_story(query, context, exercise, bucket: str) -> None:
    user = query.from_user
    stories = scan_library().get(bucket, [])
    if not stories:
        await query.edit_message_text(
            "🎧 No stories of that length yet. Try another one!",
            reply_markup=exercise.get_length_keyboard(list(scan_library().keys())),
        )
        return

    heard = await _get_heard(user.id)
    story = pick_story(stories, heard)

    # Clear any leftover audio from an abandoned session first — two story
    # audios in the chat = Telegram autoplays one into the other.
    await _delete_audio_message(context, query.message.chat_id, _state(context))

    await query.edit_message_text(
        f"🎧 *{story.title}* ({LENGTH_BUCKETS[bucket]})\n\n"
        "Press play, close your eyes, and visualize every detail.\n"
        "Tap the button below when the story ends.",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Send the audio — by cached Telegram file_id when we have one, otherwise
    # upload the file and cache the id it gets.
    file_id = get_cached_file_id(story)
    try:
        if file_id:
            audio_msg = await context.bot.send_audio(
                chat_id=query.message.chat_id, audio=file_id, title=story.title,
            )
        else:
            with open(story.path, "rb") as f:
                audio_msg = await context.bot.send_audio(
                    chat_id=query.message.chat_id, audio=f,
                    title=story.title, filename=story.path.name,
                )
            save_file_id(story, audio_msg.audio.file_id)
    except Exception as e:
        logger.error(f"Failed to send audio {story.story_id}: {e}")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="⚠️ Couldn't load that story. Please try again.",
            reply_markup=exercise.get_completion_keyboard(),
        )
        return

    offer_quiz = is_flag_enabled(AUDIO_VIZ_QUIZ_ENABLED_KEY) and story.has_quiz
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            "👁 _Visualizing? Colors, movement, textures, sounds..._"
            + ("\n\nWhen it ends: take a quick detail test, or just finish."
               if offer_quiz else "")
        ),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=exercise.get_listening_keyboard(offer_quiz),
    )

    state = _state(context)
    state["audio_story_id"] = story.story_id
    state["audio_bucket"] = bucket
    state["audio_title"] = story.title
    state["audio_questions"] = story.questions
    state["audio_msg_id"] = audio_msg.message_id
    state.pop("audio_quiz_items", None)


async def _finish_passive(query, context, exercise) -> None:
    state = _state(context)
    story_id = state.get("audio_story_id")
    bucket = state.get("audio_bucket", "?")
    if not story_id:
        await query.edit_message_text(
            "Session expired. Start a new story!",
            reply_markup=exercise.get_completion_keyboard(),
        )
        return

    await _delete_audio_message(context, query.message.chat_id, state)

    try:
        await _save_session(query.from_user.id, story_id, bucket, quiz_taken=False)
        await _mark_heard(query.from_user.id, story_id)
    except Exception as e:
        logger.error(f"Failed to save audio session: {e}")

    state.pop("audio_story_id", None)
    await query.edit_message_text(
        "🎧 *Story complete!*\n\n"
        "Nice work — every vividly imagined scene strengthens your "
        "visualization. Come back for another story anytime.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=exercise.get_completion_keyboard(),
    )


async def _start_quiz(query, context) -> None:
    state = _state(context)
    questions = state.get("audio_questions") or []
    if not state.get("audio_story_id") or not questions:
        await query.edit_message_text("Session expired. Start a new story!")
        return

    # Delete the audio message so answers come from memory, not re-listening
    # (same anti-cheat idea as deleting typed answers in word-memo tests).
    await _delete_audio_message(context, query.message.chat_id, state)

    # Shuffle each question's options once, remapping the correct index.
    items = []
    for q in questions:
        order = list(range(len(q["options"])))
        random.shuffle(order)
        items.append({
            "q": q["q"],
            "options": [q["options"][i] for i in order],
            "answer": order.index(q["answer"]),
        })
    state["audio_quiz_items"] = items
    state["audio_quiz_index"] = 0
    state["audio_quiz_results"] = []

    await _send_quiz_question(query, state)


def _quiz_keyboard(item: dict, q_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(opt, callback_data=f"audio_viz:ans:{q_idx}:{i}")]
        for i, opt in enumerate(item["options"])
    ])


async def _send_quiz_question(query, state: dict) -> None:
    idx = state["audio_quiz_index"]
    items = state["audio_quiz_items"]
    item = items[idx]
    await query.edit_message_text(
        f"❓ *Question {idx + 1}/{len(items)}*\n\n{item['q']}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_quiz_keyboard(item, idx),
    )


async def _record_quiz_answer(query, context, exercise, parts: list[str]) -> None:
    state = _state(context)
    items = state.get("audio_quiz_items")
    if not items:
        await query.edit_message_text("Session expired. Start a new story!")
        return

    q_idx, opt_idx = int(parts[2]), int(parts[3])
    if q_idx != state.get("audio_quiz_index"):
        return  # stale tap on an already-answered question

    item = items[q_idx]
    state["audio_quiz_results"].append({
        "q": item["q"],
        "picked": item["options"][opt_idx],
        "expected": item["options"][item["answer"]],
        "correct": opt_idx == item["answer"],
    })
    state["audio_quiz_index"] = q_idx + 1

    if state["audio_quiz_index"] < len(items):
        await _send_quiz_question(query, state)
        return

    await _show_quiz_results(query, context, exercise, state)


async def _show_quiz_results(query, context, exercise, state: dict) -> None:
    results = state["audio_quiz_results"]
    story_id = state.get("audio_story_id", "?")
    bucket = state.get("audio_bucket", "?")
    title = state.get("audio_title", "Story")
    score = sum(1 for r in results if r["correct"])
    total = len(results)

    streak_text = None
    try:
        await _save_session(
            query.from_user.id, story_id, bucket,
            quiz_taken=True, score=score, max_score=total,
        )
        await _mark_heard(query.from_user.id, story_id)
        # Streak counts only for quiz sessions — the active variant of the
        # exercise. Passive listens deliberately don't feed the streak.
        async with get_session() as session:
            streak_info = await UserRepository(session).update_streak(query.from_user.id)
        if streak_info["is_first_today"]:
            s = streak_info["streak"]
            if s == 1:
                streak_text = "📅 *Day 1 — streak started!*"
            elif s == streak_info["longest"]:
                streak_text = f"🔥 *{s}-day streak — new record!*"
            else:
                streak_text = f"🔥 *{s}-day streak!* Keep it up!"
    except Exception as e:
        logger.error(f"Failed to save audio quiz session: {e}")

    lines = [
        f"📊 *Detail Test — {title}*",
        f"Score: *{score}/{total}*\n",
    ]
    if streak_text:
        lines.append(streak_text)
        lines.append("")
    for i, r in enumerate(results, 1):
        mark = "✅" if r["correct"] else "❌"
        line = f"{i}. {r['q']}  {mark}"
        if not r["correct"]:
            line += f"\n      _{r['picked']}_ → correct: *{r['expected']}*"
        lines.append(line)
    if score == total:
        lines.append("\n🎉 *Perfect recall — that's vivid visualization!*")

    for key in ("audio_story_id", "audio_questions", "audio_quiz_items",
                "audio_quiz_index", "audio_quiz_results"):
        state.pop(key, None)

    await query.edit_message_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
        reply_markup=exercise.get_completion_keyboard(),
    )
