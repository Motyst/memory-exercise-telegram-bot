# Mental Training Bot 🧠

A Telegram bot for mental training exercises, designed with scalability and extensibility in mind.

## Features

### Current Exercises
- **Word Memorization**: Train visual memory by memorizing word pairs
  - 3 difficulty levels (Beginner, Intermediate, Advanced)
  - Configurable number of pairs (5-100)
  - Text and/or image output formats

### Architecture Highlights
- **Modular Exercise System**: Easy to add new exercises
- **User Database**: Track users, subscriptions, and progress
- **Session Tracking**: Monitor exercise completion and statistics
- **Async Architecture**: Non-blocking operations throughout

## Project Structure

```
mental_training_bot/
├── main.py                 # Entry point
├── requirements.txt        # Dependencies
├── .env.example           # Environment template
│
├── bot/                   # Telegram bot logic
│   ├── __init__.py       # Bot setup and initialization
│   └── handlers.py       # Command and callback handlers
│
├── config/               # Configuration
│   ├── __init__.py
│   └── settings.py       # Pydantic settings
│
├── database/             # Database layer
│   ├── __init__.py
│   ├── models.py         # SQLAlchemy models
│   ├── connection.py     # Async DB connection
│   └── repositories.py   # Data access patterns
│
├── exercises/            # Exercise modules
│   ├── __init__.py
│   ├── base.py           # Base exercise class
│   ├── registry.py       # Exercise registry
│   └── word_memorization.py  # Word memo exercise
│
├── data/                 # Word lists and data
│   ├── nouns.json
│   ├── verbs.json
│   └── adjectives.json
│
└── utils/                # Utility functions
    └── __init__.py
```

## Setup

### 1. Create a Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the bot token you receive

### 2. Configure Environment

```bash
# Copy the example env file
cp .env.example .env

# Edit .env and add your bot token
TELEGRAM_BOT_TOKEN=your_actual_token_here
```

### 3. Install Dependencies

```bash
# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 4. Run the Bot

```bash
python main.py
```

## Usage

### Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot and see main menu |
| `/help` | Show help message |
| `/stats` | View your training statistics |
| `/exercises` | List all available exercises |

### Word Memorization Exercise

1. Select "Word Memorization" from the menu
2. Choose difficulty:
   - 🟢 **Beginner**: Nouns only
   - 🟡 **Intermediate**: Nouns + Verbs
   - 🔴 **Advanced**: Nouns + Verbs + Adjectives
3. Select number of word pairs (5-100)
4. Choose output format:
   - 📝 Text Only
   - 🖼️ Image Only
   - 📝🖼️ Both
5. Study the pairs and memorize them!

## Adding New Exercises

The bot is designed to be easily extensible. To add a new exercise:

### 1. Create Exercise Class

Create a new file in `exercises/` (e.g., `number_sequence.py`):

```python
from .base import BaseExercise, Difficulty, ExerciseResult

class NumberSequenceExercise(BaseExercise):
    name = "Number Sequence"
    description = "Memorize sequences of numbers"
    exercise_type = "num_seq"
    
    def get_intro_message(self) -> str:
        return "..."
    
    def get_difficulty_keyboard(self):
        # Return InlineKeyboardMarkup
        pass
    
    def get_parameter_keyboard(self, difficulty):
        # Return InlineKeyboardMarkup
        pass
    
    async def generate(self, difficulty, parameters) -> ExerciseResult:
        # Generate exercise content
        pass
```

### 2. Register the Exercise

In `exercises/registry.py`:

```python
from .number_sequence import NumberSequenceExercise
ExerciseRegistry.register(NumberSequenceExercise)
```

### 3. Add Callback Handler

In `bot/handlers.py`, add handling for your exercise's callbacks.

## Database Schema

### Users Table
- Telegram user info
- Subscription tier (FREE, BASIC, PREMIUM)
- Preferences (JSON)

### Exercise Sessions Table
- Exercise type and difficulty
- Parameters (JSON)
- Score and completion status
- Timestamps

### Word Lists Table
- Custom word lists
- Word type classification
- Difficulty scoring

## Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | - | Your bot token from BotFather |
| `DATABASE_URL` | `sqlite+aiosqlite:///./mental_training.db` | Database connection string |
| `BOT_NAME` | `MentalTrainingBot` | Bot display name |
| `MAX_WORD_PAIRS` | `100` | Maximum word pairs per request |
| `DEFAULT_WORD_PAIRS` | `10` | Default number of pairs |

## Future Roadmap

- [ ] More exercise types (number sequences, pattern recognition, mental math)
- [ ] Quiz/verification mode to test memorization
- [ ] Spaced repetition system
- [ ] Progress graphs and detailed statistics
- [ ] Subscription system with premium features
- [ ] Leaderboards and achievements
- [ ] Multi-language support
- [ ] Custom word list uploads

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License - feel free to use and modify for your own projects.
