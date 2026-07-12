# Mental Training Bot 🧠

Telegram bot for brain training exercises. Currently features a full Word Memorization exercise with spaced repetition, streaks, and adaptive difficulty.

## Features

### Word Memorization
- **Modes**: Training (study only) | Test (study → timed disappear → quiz)
- **Difficulty**: Beginner (everyday concrete nouns) | Intermediate (all nouns + verbs) | Advanced (+adjectives)
- **Pair counts**: 5, 10, 15, 20, 30, 50, 75, 100
- Per-question timer with auto-skip on timeout
- Fuzzy matching (Levenshtein ≤2 edits, disabled for short words)
- Retry Mistakes — re-quizzes wrong answers, merges score accurately
- Reverse quiz — flips prompt/answer columns
- Speed mode — normal (5s/pair) or fast (2.5s/pair) study time
- Progressive difficulty suggestions at ≥90% score
- Placement test for new users — one 2-minute calibration round recommends a starting level
- Personal best tracking with notifications
- Anti-repeat word selection (rolling 200-word window)
- **Daily streak tracking** — shown after every test and in `/stats`

### Architecture
- Modular exercise system — easy to add new exercises (see below)
- Async throughout (python-telegram-bot + SQLAlchemy async)
- SQLite via aiosqlite (PostgreSQL migration planned)

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Main menu |
| `/help` | Help message |
| `/stats` | Training stats + current streak |
| `/history` | Last 10 sessions |
| `/exercises` | List available exercises |

## Project Structure

```
mental_training_bot/
├── main.py                        # Entry point
├── config/settings.py             # Settings, env vars, lru_cache singleton
├── bot/
│   ├── __init__.py               # Bot setup, handler registration
│   ├── handlers.py               # Callback + text-message routing
│   ├── commands.py               # Slash commands + menu callbacks
│   ├── quiz_engine.py            # Shared quiz engine (timers, scoring, results)
│   └── word_memo.py              # Word memorization flow
├── exercises/
│   ├── base.py                   # BaseExercise ABC
│   ├── registry.py               # Exercise registry
│   └── word_memorization.py      # Word memorization logic
├── database/
│   ├── models.py                 # User, ExerciseSession, SpacedRepetitionCard
│   ├── repositories.py           # DB query layer
│   └── connection.py             # Async engine + session factory
└── data/
    ├── nouns.json                # 1500+ words
    ├── verbs.json
    └── adjectives.json
```

## Setup

### 1. Create a Telegram Bot

1. Open Telegram → `@BotFather` → `/newbot`
2. Copy the token

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env — add TELEGRAM_BOT_TOKEN
```

### 3. Install Dependencies

Requires **Python 3.12** (3.14 breaks SQLAlchemy + python-telegram-bot).

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Run

```bash
python main.py
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | required | Token from BotFather |
| `DATABASE_URL` | `sqlite+aiosqlite:///./mental_training.db` | DB connection string |
| `BOT_NAME` | `MentalTrainingBot` | Bot display name |
| `MAX_WORD_PAIRS` | `100` | Max pairs per session |
| `DEFAULT_WORD_PAIRS` | `10` | Default pair count |

## Adding a New Exercise

1. Create `exercises/your_exercise.py` extending `BaseExercise`
2. Implement: `get_difficulty_keyboard()`, `get_parameter_keyboard()`, `generate()`, `get_intro_message()`
3. Set class attrs: `name`, `description`, `exercise_type`
4. Register in `exercises/registry.py`
5. Add `ExerciseType` enum value in `database/models.py`

## Roadmap

- [ ] More exercise types (number sequences, pattern recognition, mental math)
- [ ] PostgreSQL + Redis migration
- [ ] Subscription system with premium features
- [ ] Leaderboards and achievements
- [ ] Multi-language support

## License

MIT
