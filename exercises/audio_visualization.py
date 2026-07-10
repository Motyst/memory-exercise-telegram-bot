"""
Audio Visualization Exercise.

The user listens to a narrated story and visualizes it as vividly as possible.
Passive by design — the training happens in the listener's head. An optional
multiple-choice detail quiz (admin flag) offers a proxy score afterwards.

Content is file-based: drop an .mp3 into data/audio/<bucket>/ and it enters
rotation immediately (the library is rescanned on each session — no restart).
An optional sidecar .json with the same stem adds a title and quiz questions:

    data/audio/3min/lighthouse_storm.mp3
    data/audio/3min/lighthouse_storm.json   # optional
    {
      "title": "The Lighthouse Storm",
      "questions": [
        {"q": "What color was the fishing boat?",
         "options": ["Red", "Blue", "Green", "White"],
         "answer": 1}
      ]
    }

Telegram file_ids are cached in data/audio/file_ids.json after the first
upload, so each file is uploaded to Telegram only once.
"""

import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .base import BaseExercise, Difficulty, ExerciseResult

logger = logging.getLogger(__name__)

AUDIO_DIR = Path(__file__).parent.parent / "data" / "audio"
FILE_ID_CACHE = AUDIO_DIR / "file_ids.json"

# Folder name -> button label. Folders are created on demand; a bucket with no
# .mp3 files is simply not offered.
LENGTH_BUCKETS = {
    "1min": "🕐 ~1 minute",
    "3min": "🕒 ~3 minutes",
    "5min": "🕔 ~5 minutes",
}

# Rolling anti-repeat window key in user preferences.
HEARD_PREF_KEY = "audio_heard"

# Detail quiz stays a quick spot-check, not a memory exam — ask at most this
# many questions even if a sidecar ships more.
MAX_QUIZ_QUESTIONS = 3


@dataclass
class Story:
    """One audio story: an mp3 plus optional sidecar metadata."""
    story_id: str          # "<bucket>/<stem>", stable identifier
    bucket: str
    path: Path
    title: str
    questions: list[dict] = field(default_factory=list)

    @property
    def has_quiz(self) -> bool:
        return bool(self.questions)


def _title_from_stem(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").title()


def scan_library() -> dict[str, list[Story]]:
    """Scan data/audio/ for stories, grouped by length bucket.

    Called per session so new files are picked up without a restart. The
    directory is tiny (dozens of files), so a rescan costs nothing.
    """
    library: dict[str, list[Story]] = {}
    for bucket in LENGTH_BUCKETS:
        bucket_dir = AUDIO_DIR / bucket
        if not bucket_dir.is_dir():
            continue
        stories = []
        for mp3 in sorted(bucket_dir.glob("*.mp3")):
            title = _title_from_stem(mp3.stem)
            questions: list[dict] = []
            sidecar = mp3.with_suffix(".json")
            if sidecar.exists():
                try:
                    with open(sidecar, encoding="utf-8") as f:
                        meta = json.load(f)
                    title = meta.get("title", title)
                    questions = [
                        q for q in meta.get("questions", [])
                        if q.get("q") and q.get("options")
                        and isinstance(q.get("answer"), int)
                        and 0 <= q["answer"] < len(q["options"])
                    ][:MAX_QUIZ_QUESTIONS]
                except Exception as e:
                    logger.warning(f"Bad sidecar {sidecar.name}: {e}")
            stories.append(Story(
                story_id=f"{bucket}/{mp3.stem}", bucket=bucket,
                path=mp3, title=title, questions=questions,
            ))
        if stories:
            library[bucket] = stories
    return library


def pick_story(bucket_stories: list[Story], heard: list[str]) -> Story:
    """Random unheard story; falls back to any story once all are heard."""
    heard_set = set(heard)
    fresh = [s for s in bucket_stories if s.story_id not in heard_set]
    return random.choice(fresh if fresh else bucket_stories)


# ---- Telegram file_id cache ------------------------------------------------

def _cache_key(story: Story) -> str:
    # Size in the key invalidates the cache if a file is replaced in place.
    return f"{story.story_id}:{story.path.stat().st_size}"


def get_cached_file_id(story: Story) -> str | None:
    if not FILE_ID_CACHE.exists():
        return None
    try:
        with open(FILE_ID_CACHE, encoding="utf-8") as f:
            return json.load(f).get(_cache_key(story))
    except Exception:
        return None


def save_file_id(story: Story, file_id: str) -> None:
    cache = {}
    if FILE_ID_CACHE.exists():
        try:
            with open(FILE_ID_CACHE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    cache[_cache_key(story)] = file_id
    with open(FILE_ID_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


class AudioVisualizationExercise(BaseExercise):
    """Guided visualization: listen to a narrated story, picture everything."""

    name = "Audio Visualization"
    description = "Listen to a story and visualize it as vividly as you can"
    exercise_type = "audio_viz"
    menu_emoji = "🎧"
    feature_flag = "audio_viz_enabled"

    def get_intro_message(self) -> str:
        return (
            "🎧 *Audio Visualization Exercise*\n\n"
            "You'll hear a short narrated story. Close your eyes and "
            "*visualize everything* — colors, shapes, movement, sounds, "
            "textures. Make the movie in your head as vivid as you can.\n\n"
            "There's nothing to type and no timer. Just listen and picture it.\n\n"
            "How long a story do you want?"
        )

    def get_mode_keyboard(self) -> InlineKeyboardMarkup:
        """Entry keyboard (start_exercise calls this on every exercise):
        no modes here — length selection is the first and only choice."""
        return self.get_length_keyboard(list(scan_library().keys()))

    def get_length_keyboard(self, available_buckets: list[str]) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton(
                LENGTH_BUCKETS[b], callback_data=f"{self.exercise_type}:len:{b}",
            )]
            for b in LENGTH_BUCKETS if b in available_buckets
        ]
        rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
        return InlineKeyboardMarkup(rows)

    def get_listening_keyboard(self, offer_quiz: bool) -> InlineKeyboardMarkup:
        rows = []
        if offer_quiz:
            rows.append([InlineKeyboardButton(
                "🧠 Quick test on details", callback_data=f"{self.exercise_type}:quiz",
            )])
        done_label = "✅ Skip test — done" if offer_quiz else "✅ Done listening"
        rows.append([InlineKeyboardButton(done_label, callback_data=f"{self.exercise_type}:done")])
        return InlineKeyboardMarkup(rows)

    def get_completion_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Another Story", callback_data=f"{self.exercise_type}:start")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ])

    # BaseExercise ABC — this exercise has no difficulty tiers or generated
    # text content; length selection plays the parameter role.
    def get_difficulty_keyboard(self) -> InlineKeyboardMarkup:
        return self.get_length_keyboard(list(scan_library().keys()))

    def get_parameter_keyboard(self, difficulty: Difficulty) -> InlineKeyboardMarkup:
        return self.get_difficulty_keyboard()

    async def generate(self, difficulty: Difficulty, parameters: dict) -> ExerciseResult:
        bucket = parameters.get("bucket", "1min")
        heard = parameters.get("heard", [])
        stories = scan_library().get(bucket, [])
        if not stories:
            return ExerciseResult(text_content="No stories available.", additional_data={})
        story = pick_story(stories, heard)
        return ExerciseResult(
            text_content=story.title,
            additional_data={"story": story},
        )
