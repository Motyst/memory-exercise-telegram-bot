"""
Word Memorization Exercise.
Generates random word pairs for visual memorization training.
Supports Training mode (study only) and Test mode (study + quiz).
"""

import json
import random
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .base import BaseExercise, Difficulty, ExerciseResult
from config import get_settings

settings = get_settings()

# Seconds per pair for the study phase timer in test mode
SECONDS_PER_PAIR = 5

# Speed mode multiplier (halves the study time)
SPEED_MODE_MULTIPLIER = 0.5

# Per-question time limit in seconds
QUESTION_TIME_LIMIT = 15

# Maximum Levenshtein distance to accept as "close enough"
FUZZY_MAX_DISTANCE = 2


# ============================================================================
# Fuzzy matching utility
# ============================================================================

def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute the Levenshtein (edit) distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def is_fuzzy_match(answer: str, expected: str, max_distance: int = FUZZY_MAX_DISTANCE) -> bool:
    """Check if *answer* is close enough to *expected*."""
    a = answer.strip().lower()
    e = expected.strip().lower()
    if a == e:
        return True
    if len(e) <= 3:
        return False
    return levenshtein_distance(a, e) <= max_distance


# ============================================================================
# Progressive difficulty helpers
# ============================================================================

NEXT_DIFFICULTY = {
    Difficulty.BEGINNER: Difficulty.INTERMEDIATE,
    Difficulty.INTERMEDIATE: Difficulty.ADVANCED,
}

NEXT_COUNT = {5: 10, 10: 15, 15: 20, 20: 30, 30: 50, 50: 75, 75: 100}

DIFFICULTY_NAMES = {
    Difficulty.BEGINNER: "Beginner (Nouns)",
    Difficulty.INTERMEDIATE: "Intermediate (Nouns + Verbs)",
    Difficulty.ADVANCED: "Advanced (All Types)",
}


def get_progression_suggestion(
    difficulty: Difficulty, count: int, score_pct: float
) -> str | None:
    """
    Return a suggestion string if the user should level up, or None.
    Triggers at ≥ 90% score.
    """
    if score_pct < 90:
        return None

    next_diff = NEXT_DIFFICULTY.get(difficulty)
    next_cnt = NEXT_COUNT.get(count)

    suggestions = []
    if next_cnt and next_cnt <= 100:
        suggestions.append(f"try *{next_cnt} pairs*")
    if next_diff:
        suggestions.append(f"step up to *{DIFFICULTY_NAMES[next_diff]}*")

    if not suggestions:
        return None

    return "💡 You're doing great! Maybe " + " or ".join(suggestions) + "?"


class WordMemorizationExercise(BaseExercise):
    """Word memorization exercise with Training and Test modes."""

    name = "Word Memorization"
    description = "Train your visual memory by memorizing word pairs"
    exercise_type = "word_memo"

    COUNT_OPTIONS = [5, 10, 15, 20, 30, 50, 75, 100]

    def __init__(self):
        super().__init__()
        self._words_cache: dict = {}
        self._load_words()

    def _load_words(self):
        data_dir = Path(__file__).parent.parent / "data"
        word_files = {
            "nouns": data_dir / "nouns.json",
            "verbs": data_dir / "verbs.json",
            "adjectives": data_dir / "adjectives.json",
        }
        for word_type, filepath in word_files.items():
            if filepath.exists():
                with open(filepath, "r") as f:
                    data = json.load(f)
                    self._words_cache[word_type] = data.get(word_type, [])
            else:
                self._words_cache[word_type] = []

    def _get_words_for_difficulty(self, difficulty: Difficulty) -> list[str]:
        if difficulty == Difficulty.BEGINNER:
            return self._words_cache.get("nouns", [])
        elif difficulty == Difficulty.INTERMEDIATE:
            return self._words_cache.get("nouns", []) + self._words_cache.get("verbs", [])
        else:
            return (
                self._words_cache.get("nouns", [])
                + self._words_cache.get("verbs", [])
                + self._words_cache.get("adjectives", [])
            )

    # ========================================================================
    # Messages
    # ========================================================================

    def get_intro_message(self) -> str:
        return (
            "🧠 *Word Memorization Exercise*\n\n"
            "Train your visual memory by memorizing word pairs.\n\n"
            "*Modes:*\n"
            "• 📝 *Training* — Study word pairs at your own pace\n"
            "• 🎯 *Test* — Study pairs, then get quizzed on each one\n\n"
            "Select your mode:"
        )

    def get_difficulty_message(self, mode: str) -> str:
        mode_label = "📝 Training" if mode == "training" else "🎯 Test"
        return (
            f"*Mode:* {mode_label}\n\n"
            "*Difficulty levels:*\n"
            "• 🟢 Beginner — Nouns only\n"
            "• 🟡 Intermediate — Nouns + Verbs\n"
            "• 🔴 Advanced — Nouns + Verbs + Adjectives\n\n"
            "Select your difficulty:"
        )

    def get_speed_message(self, difficulty: Difficulty) -> str:
        diff_label = DIFFICULTY_NAMES[difficulty]
        normal_note = f"{SECONDS_PER_PAIR}s per pair"
        speed_note = f"{SECONDS_PER_PAIR * SPEED_MODE_MULTIPLIER:.0f}s per pair"
        return (
            f"*Difficulty:* {diff_label}\n\n"
            "Choose your study pace:\n"
            f"• 🕐 *Normal* — {normal_note}\n"
            f"• ⚡ *Speed* — {speed_note} (half time!)\n"
        )

    # ========================================================================
    # Keyboards
    # ========================================================================

    def get_mode_keyboard(self) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton("📝 Training Mode", callback_data=f"{self.exercise_type}:mode:training")],
            [InlineKeyboardButton("🎯 Test Mode", callback_data=f"{self.exercise_type}:mode:test")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]
        return InlineKeyboardMarkup(rows)

    def get_difficulty_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 Beginner (Nouns)", callback_data=f"{self.exercise_type}:diff:beginner")],
            [InlineKeyboardButton("🟡 Intermediate (Nouns + Verbs)", callback_data=f"{self.exercise_type}:diff:intermediate")],
            [InlineKeyboardButton("🔴 Advanced (All Types)", callback_data=f"{self.exercise_type}:diff:advanced")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"{self.exercise_type}:start"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ])

    def get_speed_keyboard(self) -> InlineKeyboardMarkup:
        """Normal vs Speed study pace (test mode only)."""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🕐 Normal Pace", callback_data=f"{self.exercise_type}:speed:normal")],
            [InlineKeyboardButton("⚡ Speed Mode (half time!)", callback_data=f"{self.exercise_type}:speed:fast")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"{self.exercise_type}:back_to_diff"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ])

    def get_parameter_keyboard(self, difficulty: Difficulty) -> InlineKeyboardMarkup:
        buttons = []
        row = []
        for i, count in enumerate(self.COUNT_OPTIONS):
            row.append(InlineKeyboardButton(
                f"{count} pairs", callback_data=f"{self.exercise_type}:count:{count}",
            ))
            if len(row) == 4 or i == len(self.COUNT_OPTIONS) - 1:
                buttons.append(row)
                row = []
        buttons.append([
            InlineKeyboardButton("⬅️ Back", callback_data=f"{self.exercise_type}:back_to_speed"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
        ])
        return InlineKeyboardMarkup(buttons)

    def get_parameter_keyboard_training(self, difficulty: Difficulty) -> InlineKeyboardMarkup:
        """Count keyboard for training mode (back goes to difficulty, not speed)."""
        buttons = []
        row = []
        for i, count in enumerate(self.COUNT_OPTIONS):
            row.append(InlineKeyboardButton(
                f"{count} pairs", callback_data=f"{self.exercise_type}:count:{count}",
            ))
            if len(row) == 4 or i == len(self.COUNT_OPTIONS) - 1:
                buttons.append(row)
                row = []
        buttons.append([
            InlineKeyboardButton("⬅️ Back", callback_data=f"{self.exercise_type}:back_to_diff"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
        ])
        return InlineKeyboardMarkup(buttons)

    def get_skip_keyboard(self, seconds_left: int = QUESTION_TIME_LIMIT) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(f"⏭ Skip ({seconds_left}s left)", callback_data=f"{self.exercise_type}:skip")
        ]])

    def get_results_keyboard(self, has_mistakes: bool = False) -> InlineKeyboardMarkup:
        """Results keyboard with Retry Mistakes and Reverse Quiz options."""
        rows = []

        first_row = [
            InlineKeyboardButton("🔄 Another List", callback_data=f"{self.exercise_type}:again"),
        ]
        if has_mistakes:
            first_row.append(
                InlineKeyboardButton("🔁 Retry Mistakes", callback_data=f"{self.exercise_type}:retry_mistakes")
            )
        rows.append(first_row)

        # Reverse quiz — always available after a test
        rows.append([
            InlineKeyboardButton("🔀 Reverse Quiz", callback_data=f"{self.exercise_type}:reverse_quiz"),
        ])

        rows.append([
            InlineKeyboardButton("⚙️ Change Settings", callback_data=f"{self.exercise_type}:settings"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
        ])
        return InlineKeyboardMarkup(rows)

    # ========================================================================
    # Generation
    # ========================================================================

    # Rolling window: how many recently used words to avoid re-showing
    RECENT_WORDS_WINDOW = 200

    async def generate(self, difficulty: Difficulty, parameters: dict) -> ExerciseResult:
        count = min(parameters.get("count", 10), settings.max_word_pairs)
        all_words = self._get_words_for_difficulty(difficulty)
        recent: list[str] = parameters.get("recent_words", [])

        # Prefer words not seen recently; fall back to full pool if needed
        recent_set = set(w.lower() for w in recent)
        fresh = [w for w in all_words if w.lower() not in recent_set]
        pool = fresh if len(fresh) >= count * 2 else all_words

        if len(pool) < count * 2:
            selected_words = random.choices(pool, k=count * 2)
        else:
            selected_words = random.sample(pool, count * 2)

        pairs = [(selected_words[i], selected_words[i + count]) for i in range(count)]
        return ExerciseResult(
            text_content=self._format_pairs_text(pairs, difficulty),
            additional_data={"pairs": pairs, "difficulty": difficulty.value, "count": count},
        )

    # ========================================================================
    # Text formatting
    # ========================================================================

    def _format_pairs_text(self, pairs: list[tuple[str, str]], difficulty: Difficulty) -> str:
        lines = [
            f"📋 *Word Memorization - {DIFFICULTY_NAMES[difficulty]}*",
            f"Total pairs: {len(pairs)}\n",
        ]
        for i, (word1, word2) in enumerate(pairs, 1):
            lines.append(f"{i}. *{word1}* — {word2}")
            if i % 10 == 0 and i < len(pairs):
                lines.append("———————————")
        return "\n".join(lines)

    def format_pairs_text_for_test(
        self, pairs, difficulty, countdown_seconds, speed_mode=False,
    ) -> str:
        base = self._format_pairs_text(pairs, difficulty)
        speed_label = " ⚡ *SPEED MODE*" if speed_mode else ""
        base += (
            f"\n\n⏱ *Test Mode*{speed_label} — You have *{countdown_seconds} seconds* to memorize.\n"
            "The list will disappear and you'll be quizzed!\n"
            f"Each question has a *{QUESTION_TIME_LIMIT}s* time limit."
        )
        return base

    def format_test_prompt(self, shown_word: str, current: int, total: int) -> str:
        return (
            f"❓ *Question {current}/{total}*\n\n"
            f"What was paired with:  *{shown_word}*  ?\n\n"
            f"_Type your answer, or tap Skip. ({QUESTION_TIME_LIMIT}s)_"
        )

    def format_test_results(
        self, pairs, results, difficulty,
        personal_best_text: str | None = None,
        progression_text: str | None = None,
        streak_text: str | None = None,
    ) -> str:
        correct_count = sum(1 for r in results if r["correct"])
        total = len(results)

        diff_label = DIFFICULTY_NAMES.get(difficulty, "Unknown") if difficulty else "Unknown"
        lines = [
            f"📊 *Test Results — {diff_label}*",
            f"Score: *{correct_count}/{total}*\n",
        ]

        # Streak notification
        if streak_text:
            lines.append(streak_text)
            lines.append("")

        # Personal best notification (#6)
        if personal_best_text:
            lines.append(personal_best_text)
            lines.append("")

        lines.append("*Original pairs with your answers:*\n")

        result_by_pair = {r["pair_index"]: r for r in results}
        for i, (word1, word2) in enumerate(pairs):
            r = result_by_pair.get(i)
            if r and r["correct"]:
                mark = "✅~" if r.get("fuzzy") else "✅"
            else:
                mark = "❌"
            line = f"{i + 1}. *{word1}* — {word2}  {mark}"
            if r and not r["correct"]:
                line += f"  (you said: _{r['answer']}_)"
            lines.append(line)
            if (i + 1) % 10 == 0 and (i + 1) < len(pairs):
                lines.append("———————————")

        # Summary
        if correct_count == total:
            lines.append("\n🎉 *Perfect score! Amazing memory!*")
        elif correct_count >= total * 0.8:
            lines.append("\n👏 *Great job! Almost perfect!*")
        elif correct_count >= total * 0.5:
            lines.append("\n💪 *Good effort! Keep practicing!*")
        else:
            lines.append("\n🔄 *Keep training — you'll improve!*")

        # Progressive difficulty suggestion (#3)
        if progression_text:
            lines.append(progression_text)

        # Legend for fuzzy
        if any(r.get("fuzzy") for r in results if r["correct"]):
            lines.append("\n_✅~ = accepted with minor typo_")

        return "\n".join(lines)
