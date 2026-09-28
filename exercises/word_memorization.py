"""
Word Memorization Exercise.
Generates random word pairs for visual memorization training.
Supports Training mode (study only) and Test mode (study + quiz).
"""

import json
import random
from functools import lru_cache
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown

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

# Typo tolerance scales with word length: a short word has no room for two
# typos before it is a different word ("lamp" -> "limb" is two edits).
FUZZY_EXACT_MAX_LEN = 3   # words up to this long: exact match only
FUZZY_SHORT_MAX_LEN = 5   # words up to this long: 1 edit
FUZZY_MAX_DISTANCE = 2    # longer words: up to 2 edits

DATA_DIR = Path(__file__).parent.parent / "data"
WORD_FILES = ("concrete_nouns", "nouns", "verbs", "adjectives")


# ============================================================================
# Word data
# ============================================================================

@lru_cache(maxsize=1)
def _read_word_files() -> dict[str, tuple[str, ...]]:
    """Word lists by type, read once. A missing file loads as empty."""
    words = {}
    for word_type in WORD_FILES:
        path = DATA_DIR / f"{word_type}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                words[word_type] = tuple(json.load(f).get(word_type, []))
        else:
            words[word_type] = ()
    return words


@lru_cache(maxsize=1)
def _vocabulary() -> frozenset[str]:
    """Every word the exercise can show, lowercased."""
    return frozenset(w.lower() for ws in _read_word_files().values() for w in ws)


# ============================================================================
# Fuzzy matching utility
# ============================================================================

def edit_distance(s1: str, s2: str) -> int:
    """Optimal-string-alignment distance: insert, delete, substitute, or swap
    two adjacent letters — each costs 1. Plain Levenshtein charges a swap
    ("stoen") as 2, which ate the whole budget of a short word."""
    d = [[0] * (len(s2) + 1) for _ in range(len(s1) + 1)]
    for i in range(len(s1) + 1):
        d[i][0] = i
    for j in range(len(s2) + 1):
        d[0][j] = j
    for i in range(1, len(s1) + 1):
        for j in range(1, len(s2) + 1):
            cost = s1[i - 1] != s2[j - 1]
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and s1[i - 1] == s2[j - 2] and s1[i - 2] == s2[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def _fuzzy_allowance(length: int) -> int:
    if length <= FUZZY_EXACT_MAX_LEN:
        return 0
    if length <= FUZZY_SHORT_MAX_LEN:
        return 1
    return FUZZY_MAX_DISTANCE


def is_fuzzy_match(answer: str, expected: str) -> bool:
    """Check if *answer* is *expected* give or take a typo."""
    a = answer.strip().lower()
    e = expected.strip().lower()
    if a == e:
        return True
    # A different real word is a wrong answer, not a typo: "house" for
    # "horse" is one edit away but recalls the wrong thing.
    if a in _vocabulary():
        return False
    allowed = _fuzzy_allowance(len(e))
    if abs(len(a) - len(e)) > allowed:
        return False
    return edit_distance(a, e) <= allowed


# ============================================================================
# Progressive difficulty helpers
# ============================================================================

NEXT_COUNT = {5: 10, 10: 15, 15: 20, 20: 30, 30: 50, 50: 75, 75: 100}

DIFFICULTY_NAMES = {
    Difficulty.BEGINNER: "Beginner (Nouns)",
    Difficulty.INTERMEDIATE: "Intermediate (Nouns + Verbs)",
    Difficulty.ADVANCED: "Advanced (All Types)",
}

# Format = what the user memorizes: linked pairs or one ordered list.
FORMAT_NAMES = {"pairs": "🔗 Word Pairs", "list": "📜 Word List"}
FORMAT_UNITS = {"pairs": "pairs", "list": "words"}

DIFF_EMOJI = {"beginner": "🟢", "intermediate": "🟡", "advanced": "🔴"}


def get_placement_recommendation(score_pct: float) -> tuple[Difficulty, int]:
    """Map placement-test score to a recommended (difficulty, pair count)."""
    if score_pct < 50:
        return Difficulty.BEGINNER, 5
    if score_pct < 75:
        return Difficulty.BEGINNER, 10
    if score_pct < 90:
        return Difficulty.INTERMEDIATE, 10
    return Difficulty.ADVANCED, 10


# Progression ladder: at ≥90% the results keyboard offers ONE next step —
# more words (⬆️ Level up button) before speed (⚡ Speed run button). The old
# text suggestion line was removed deliberately; the buttons carry it now.
# Flip to False to hide the ⚡ Speed run button everywhere (bot/quiz_engine.py
# + bot/word_memo.py speed_run handler go dead but harmless).
PROGRESSION_LADDER = True


# Answers the engine records for a non-answer, mapped to the results wording —
# "(you said: (skipped))" reads like the user typed it.
_NON_ANSWERS = {"(skipped)": "_skipped_", "(timed out)": "_timed out_"}

# Typed answers echoed on the results screen are cut to this length — one
# pasted paragraph would otherwise push the message past Telegram's limit.
ANSWER_NOTE_MAX_CHARS = 40


def _answer_note(result: dict | None) -> str:
    """Trailing note for one results line: what the user actually answered.

    The answer is free text, so it is escaped and shown outside any entity
    (legacy Markdown can't escape inside one): a single "_" in an answer
    used to fail the whole results message.
    """
    if not result or result["correct"]:
        return ""
    answer = (result.get("answer") or "").strip()
    if answer in _NON_ANSWERS:
        return f"  ({_NON_ANSWERS[answer]})"
    if not answer:
        return "  (_no answer_)"
    if len(answer) > ANSWER_NOTE_MAX_CHARS:
        answer = answer[:ANSWER_NOTE_MAX_CHARS].rstrip() + "…"
    return f"  (you said: {escape_markdown(answer, version=1)})"


def _dedupe(words: list[str]) -> list[str]:
    """Drop repeated words (case-insensitive), keeping first-seen order.

    The word files overlap ("answer" is both a noun and a verb) and repeat
    entries inside a file; a word appearing twice in one test makes a
    question with two right answers, of which only one is accepted.
    """
    seen: set[str] = set()
    unique = []
    for w in words:
        key = w.lower()
        if key not in seen:
            seen.add(key)
            unique.append(w)
    return unique


def should_offer_speed_run(count: int, score_pct: float, speed_mode: bool) -> bool:
    """⚡ Speed run button appears when the ladder's next rung is speed mode."""
    return (
        PROGRESSION_LADDER
        and score_pct >= 90
        and not speed_mode
        and NEXT_COUNT.get(count) is None
    )


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
        self._words_cache = {k: list(v) for k, v in _read_word_files().items()}

    def _get_words_for_difficulty(self, difficulty: Difficulty) -> list[str]:
        if difficulty == Difficulty.BEGINNER:
            # Concrete, well-known objects only — easiest to visualize.
            # Fall back to the full noun list if the file is missing.
            words = self._words_cache.get("concrete_nouns") or self._words_cache.get("nouns", [])
        elif difficulty == Difficulty.INTERMEDIATE:
            words = self._words_cache.get("nouns", []) + self._words_cache.get("verbs", [])
        else:
            words = (
                self._words_cache.get("nouns", [])
                + self._words_cache.get("verbs", [])
                + self._words_cache.get("adjectives", [])
            )
        return _dedupe(words)

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
            "• 🟢 Beginner — Nouns\n"
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
            [InlineKeyboardButton("🟢 Beginner (Nouns)", callback_data=f"{self.exercise_type}:diff:beginner")],
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

    def get_skip_keyboard(
        self, seconds_left: int = QUESTION_TIME_LIMIT, question_index: int | None = None,
    ) -> InlineKeyboardMarkup:
        """*question_index* rides in the callback so a double-tapped or stale
        Skip only ever skips the question it was shown on."""
        data = f"{self.exercise_type}:skip"
        if question_index is not None:
            data += f":{question_index}"
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(f"⏭ Skip ({seconds_left}s left)", callback_data=data)
        ]])

    def get_results_keyboard(
        self, has_mistakes: bool = False,
        next_count: int | None = None, fmt: str = "pairs",
        offer_speed_run: bool = False,
        offer_leaderboard_join: bool = False,
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

        # Progression ladder: speed rung — same test, speed mode on
        if offer_speed_run:
            rows.append([InlineKeyboardButton(
                "⚡ Speed run: same test, half time",
                callback_data=f"{self.exercise_type}:speed_run",
            )])

        # Reverse quiz — always available after a test
        rows.append([
            InlineKeyboardButton("🔀 Reverse Quiz", callback_data=f"{self.exercise_type}:reverse_quiz"),
        ])

        # Paired with the "you'd rank #N" nudge in the results text
        if offer_leaderboard_join:
            rows.append([
                InlineKeyboardButton("✋ Join leaderboard", callback_data="lb:join"),
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
            "The list will disappear, then you'll rebuild it in order: first "
            "recall the opening word, then each word is shown and you recall "
            "the word that came *right after* it.\n"
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
        elif direction == "first":
            # Opening question of a forward list walk — nothing shown, so the
            # first word is tested too instead of being handed over.
            question = "What was the *1st* word in the list?"
        elif direction == "last":
            question = "What was the *last* word in the list?"
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
        streak_text: str | None = None,
        compact: bool = False,
        fmt: str = "pairs",
    ) -> str:
        """compact=True (user setting): only score header, pairs and typo legend.
        fmt="list": *pairs* holds the ordered word list and pair_index is the
        position of the word the question asked for (0 = the opening word), so
        every position gets its own line."""
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
            # One line per position: the word that had to be recalled there.
            for i, word in enumerate(pairs):
                r = result_by_pair.get(i)
                if r and r["correct"]:
                    mark = "✅~" if r.get("fuzzy") else "✅"
                else:
                    mark = "❌"
                line = f"{i + 1}. *{word}*  {mark}{_answer_note(r)}"
                lines.append(line)
                if (i + 1) % 10 == 0 and (i + 1) < len(pairs):
                    lines.append("———————————")
        else:
            for i, (word1, word2) in enumerate(pairs):
                r = result_by_pair.get(i)
                if r and r["correct"]:
                    mark = "✅~" if r.get("fuzzy") else "✅"
                else:
                    mark = "❌"
                line = f"{i + 1}. *{word1}* — {word2}  {mark}{_answer_note(r)}"
                lines.append(line)
                if (i + 1) % 10 == 0 and (i + 1) < len(pairs):
                    lines.append("———————————")

        # Legend for fuzzy
        if any(r.get("fuzzy") for r in results if r["correct"]):
            lines.append("\n_✅~ = accepted with minor typo_")

        return "\n".join(lines)
