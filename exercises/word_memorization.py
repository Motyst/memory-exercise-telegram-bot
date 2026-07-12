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

# Seconds per word for list-format study (single words, but order must stick)
SECONDS_PER_WORD = 3

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
    Difficulty.BEGINNER: "Beginner (Everyday Objects)",
    Difficulty.INTERMEDIATE: "Intermediate (Nouns + Verbs)",
    Difficulty.ADVANCED: "Advanced (All Types)",
}

# Format = what the user memorizes: linked pairs or one ordered list.
FORMAT_NAMES = {"pairs": "🔗 Word Pairs", "list": "📜 Word List"}
FORMAT_UNITS = {"pairs": "pairs", "list": "words"}


def get_progression_suggestion(
    difficulty: Difficulty, count: int, score_pct: float, fmt: str = "pairs"
) -> str | None:
    """
    Return a suggestion string if the user should level up, or None.
    Triggers at ≥ 90% score.
    """
    if score_pct < 90:
        return None

    next_diff = NEXT_DIFFICULTY.get(difficulty)
    next_cnt = NEXT_COUNT.get(count)
    unit = FORMAT_UNITS.get(fmt, "pairs")

    suggestions = []
    if next_cnt and next_cnt <= 100:
        suggestions.append(f"try *{next_cnt} {unit}*")
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
            "concrete_nouns": data_dir / "concrete_nouns.json",
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
            # Concrete, well-known objects only — easiest to visualize.
            # Fall back to the full noun list if the file is missing.
            return self._words_cache.get("concrete_nouns") or self._words_cache.get("nouns", [])
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
            "Train your visual memory.\n\n"
            "*What to memorize:*\n"
            "• 🔗 *Word Pairs* — memorize pairs, recall the partner word\n"
            "• 📜 *Word List* — memorize one ordered list, then recall it "
            "word by word from the start\n\n"
            "Choose a format:"
        )

    def get_mode_message(self, fmt: str) -> str:
        fmt_label = FORMAT_NAMES.get(fmt, FORMAT_NAMES["pairs"])
        unit = FORMAT_UNITS.get(fmt, "pairs")
        return (
            f"*Format:* {fmt_label}\n\n"
            "*Modes:*\n"
            f"• 📝 *Training* — Study {unit} at your own pace\n"
            f"• 🎯 *Test* — Study {unit}, then get quizzed\n\n"
            "Select your mode:"
        )

    def get_difficulty_message(self, mode: str) -> str:
        mode_label = "📝 Training" if mode == "training" else "🎯 Test"
        return (
            f"*Mode:* {mode_label}\n\n"
            "*Difficulty levels:*\n"
            "• 🟢 Beginner — Everyday objects (easy to picture)\n"
            "• 🟡 Intermediate — All nouns + Verbs\n"
            "• 🔴 Advanced — Nouns + Verbs + Adjectives\n\n"
            "Select your difficulty:"
        )

    def get_speed_message(self, difficulty: Difficulty, fmt: str = "pairs") -> str:
        diff_label = DIFFICULTY_NAMES[difficulty]
        per = SECONDS_PER_WORD if fmt == "list" else SECONDS_PER_PAIR
        item = "word" if fmt == "list" else "pair"
        normal_note = f"{per}s per {item}"
        speed_note = f"{per * SPEED_MODE_MULTIPLIER:.1f}s per {item}".replace(".0s", "s")
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
        """Entry keyboard: format selection (pairs vs list)."""
        rows = [
            [InlineKeyboardButton("🔗 Word Pairs", callback_data=f"{self.exercise_type}:format:pairs")],
            [InlineKeyboardButton("📜 Word List", callback_data=f"{self.exercise_type}:format:list")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]
        return InlineKeyboardMarkup(rows)

    def get_mode_select_keyboard(self) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton("📝 Training Mode", callback_data=f"{self.exercise_type}:mode:training")],
            [InlineKeyboardButton("🎯 Test Mode", callback_data=f"{self.exercise_type}:mode:test")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"{self.exercise_type}:start"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]
        return InlineKeyboardMarkup(rows)

    def get_difficulty_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 Beginner (Everyday Objects)", callback_data=f"{self.exercise_type}:diff:beginner")],
            [InlineKeyboardButton("🟡 Intermediate (Nouns + Verbs)", callback_data=f"{self.exercise_type}:diff:intermediate")],
            [InlineKeyboardButton("🔴 Advanced (All Types)", callback_data=f"{self.exercise_type}:diff:advanced")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"{self.exercise_type}:back_to_mode"),
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

    def _build_count_keyboard(self, back_action: str, fmt: str = "pairs") -> InlineKeyboardMarkup:
        unit = FORMAT_UNITS.get(fmt, "pairs")
        buttons = []
        row = []
        for i, count in enumerate(self.COUNT_OPTIONS):
            row.append(InlineKeyboardButton(
                f"{count} {unit}", callback_data=f"{self.exercise_type}:count:{count}",
            ))
            if len(row) == 4 or i == len(self.COUNT_OPTIONS) - 1:
                buttons.append(row)
                row = []
        buttons.append([
            InlineKeyboardButton("⬅️ Back", callback_data=f"{self.exercise_type}:{back_action}"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
        ])
        return InlineKeyboardMarkup(buttons)

    def get_parameter_keyboard(self, difficulty: Difficulty, fmt: str = "pairs") -> InlineKeyboardMarkup:
        return self._build_count_keyboard("back_to_speed", fmt)

    def get_parameter_keyboard_training(self, difficulty: Difficulty, fmt: str = "pairs") -> InlineKeyboardMarkup:
        """Count keyboard for training mode (back goes to difficulty, not speed)."""
        return self._build_count_keyboard("back_to_diff", fmt)

    def get_skip_keyboard(self, seconds_left: int = QUESTION_TIME_LIMIT) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(f"⏭ Skip ({seconds_left}s left)", callback_data=f"{self.exercise_type}:skip")
        ]])

    def get_results_keyboard(
        self, has_mistakes: bool = False,
        next_count: int | None = None, fmt: str = "pairs",
    ) -> InlineKeyboardMarkup:
        """Results keyboard with Retry Mistakes, Reverse Quiz and Level Up options."""
        rows = []

        first_row = [
            InlineKeyboardButton("🔄 Another List", callback_data=f"{self.exercise_type}:again"),
        ]
        if has_mistakes:
            first_row.append(
                InlineKeyboardButton("🔁 Retry Mistakes", callback_data=f"{self.exercise_type}:retry_mistakes")
            )
        rows.append(first_row)

        # Level up — same difficulty & settings, next size up (hidden at max)
        if next_count:
            unit = FORMAT_UNITS.get(fmt, "pairs")
            rows.append([InlineKeyboardButton(
                f"⬆️ Level up: {next_count} {unit}",
                callback_data=f"{self.exercise_type}:next_count:{next_count}",
            )])

        # Reverse quiz — always available after a test
        rows.append([
            InlineKeyboardButton("🔀 Reverse Quiz", callback_data=f"{self.exercise_type}:reverse_quiz"),
        ])

        rows.append([
            InlineKeyboardButton("⚙️ Change Settings", callback_data=f"{self.exercise_type}:settings"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu"),
        ])
        return InlineKeyboardMarkup(rows)

    def get_completion_keyboard(
        self, next_count: int | None = None, fmt: str = "pairs",
    ) -> InlineKeyboardMarkup:
        """Training-mode completion keyboard, with optional Level Up button."""
        rows = [[
            InlineKeyboardButton("🔄 Another List", callback_data=f"{self.exercise_type}:again"),
            InlineKeyboardButton("⚙️ Change Settings", callback_data=f"{self.exercise_type}:settings"),
        ]]
        if next_count:
            unit = FORMAT_UNITS.get(fmt, "pairs")
            rows.append([InlineKeyboardButton(
                f"⬆️ Level up: {next_count} {unit}",
                callback_data=f"{self.exercise_type}:next_count:{next_count}",
            )])
        rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
        return InlineKeyboardMarkup(rows)

    # ========================================================================
    # Generation
    # ========================================================================

    # Rolling window: how many recently used words to avoid re-showing
    RECENT_WORDS_WINDOW = 200

    async def generate(self, difficulty: Difficulty, parameters: dict) -> ExerciseResult:
        count = min(parameters.get("count", 10), settings.max_word_pairs)
        fmt = parameters.get("format", "pairs")
        all_words = self._get_words_for_difficulty(difficulty)
        recent: list[str] = parameters.get("recent_words", [])

        needed = count if fmt == "list" else count * 2

        # Prefer words not seen recently; fall back to full pool if needed
        recent_set = set(w.lower() for w in recent)
        fresh = [w for w in all_words if w.lower() not in recent_set]
        pool = fresh if len(fresh) >= needed else all_words

        if len(pool) < needed:
            selected_words = random.choices(pool, k=needed)
        else:
            selected_words = random.sample(pool, needed)

        if fmt == "list":
            return ExerciseResult(
                text_content=self._format_list_text(selected_words, difficulty),
                additional_data={"words": selected_words, "difficulty": difficulty.value, "count": count},
            )

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

    def _format_list_text(self, words: list[str], difficulty: Difficulty) -> str:
        lines = [
            f"📜 *Word List - {DIFFICULTY_NAMES[difficulty]}*",
            f"Total words: {len(words)} — *order matters!*\n",
        ]
        for i, word in enumerate(words, 1):
            lines.append(f"{i}. *{word}*")
            if i % 10 == 0 and i < len(words):
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

    def format_list_text_for_test(
        self, words, difficulty, countdown_seconds, speed_mode=False,
    ) -> str:
        base = self._format_list_text(words, difficulty)
        speed_label = " ⚡ *SPEED MODE*" if speed_mode else ""
        base += (
            f"\n\n⏱ *Test Mode*{speed_label} — You have *{countdown_seconds} seconds* to memorize.\n"
            "The list will disappear, then you'll walk through it in order: "
            "each word is shown and you recall the word that came *right after* it.\n"
            f"Each question has a *{QUESTION_TIME_LIMIT}s* time limit."
        )
        return base

    def format_test_prompt(
        self, shown_word: str, current: int, total: int, direction: str | None = None,
    ) -> str:
        if direction == "next":
            question = f"Which word came *right after*:  *{shown_word}*  ?"
        elif direction == "prev":
            question = f"Which word came *right before*:  *{shown_word}*  ?"
        else:
            question = f"What was paired with:  *{shown_word}*  ?"
        return (
            f"❓ *Question {current}/{total}*\n\n"
            f"{question}\n\n"
            f"_Type your answer, or tap Skip. ({QUESTION_TIME_LIMIT}s)_"
        )

    def format_test_results(
        self, pairs, results, difficulty,
        personal_best_text: str | None = None,
        progression_text: str | None = None,
        streak_text: str | None = None,
        compact: bool = False,
        fmt: str = "pairs",
    ) -> str:
        """compact=True (user setting): only score header, pairs and typo legend.
        fmt="list": *pairs* holds the ordered word list; each result covers one
        adjacent link (pair_index = position of the earlier word)."""
        correct_count = sum(1 for r in results if r["correct"])
        total = len(results)

        diff_label = DIFFICULTY_NAMES.get(difficulty, "Unknown") if difficulty else "Unknown"
        header = "Word List Results" if fmt == "list" else "Test Results"
        lines = [
            f"📊 *{header} — {diff_label}*",
            f"Score: *{correct_count}/{total}*\n",
        ]

        if not compact:
            # Streak notification
            if streak_text:
                lines.append(streak_text)
                lines.append("")

            # Personal best notification (#6)
            if personal_best_text:
                lines.append(personal_best_text)
                lines.append("")

            label = "list" if fmt == "list" else "pairs"
            lines.append(f"*Original {label} with your answers:*\n")

        result_by_pair = {r["pair_index"]: r for r in results}
        if fmt == "list":
            # One line per adjacent link: word_i → word_i+1
            for i in range(len(pairs) - 1):
                r = result_by_pair.get(i)
                if r and r["correct"]:
                    mark = "✅~" if r.get("fuzzy") else "✅"
                else:
                    mark = "❌"
                line = f"{i + 1}. *{pairs[i]}* → {pairs[i + 1]}  {mark}"
                if r and not r["correct"]:
                    line += f"  (you said: _{r['answer']}_)"
                lines.append(line)
                if (i + 1) % 10 == 0 and (i + 1) < len(pairs) - 1:
                    lines.append("———————————")
        else:
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

        if not compact:
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
