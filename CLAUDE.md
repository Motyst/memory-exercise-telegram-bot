# Mental Training Bot

Python Telegram bot for brain training exercises. Two exercises: Word Memorization (live) and Audio Visualization (feature-flagged, default off), architecture designed for easy expansion. Gamified: achievements, streaks, opt-in leaderboard. Offered as a training product for a Skool mental-training community.

## Tech Stack

- **Python 3.12** (required — 3.14 breaks SQLAlchemy + python-telegram-bot)
- **python-telegram-bot 21.x** with async polling, `concurrent_updates(True)` (required — sequential mode blocks all users on any handler await)
- **SQLAlchemy 2.0** + aiosqlite (SQLite, async, WAL mode)
- **python-dotenv** for config

## Project Structure

```
main.py                        # Entry point, logging setup, run_polling
config/settings.py             # Settings class, env vars, lru_cache singleton
bot/__init__.py                # create_application(), handler registration, startup/shutdown hooks
bot/handlers.py                # Thin router: callback_handler → CALLBACK_ROUTES, text_message_handler
bot/commands.py                # Slash commands (/start, /stats, ...) + menu/lb/settings callbacks, main-menu keyboard
bot/quiz_engine.py             # Shared quiz engine: timers, grace window, answer recording, results pipeline (save/PB/streak/achievements/XP), retry/reverse; ENGINE_EXERCISE_ENUM maps registry key → ExerciseType
bot/word_memo.py               # Word Memorization flow: format/mode/difficulty/speed/count callbacks, generation, placement test
bot/state.py                   # Per-user context state helpers, answer locks, bot-message tracking/cleanup
bot/recent_words.py            # Rolling anti-repeat window of recently shown words (user prefs)
bot/admin.py                   # /admin commands (overview, users, export, grant, codes, xp/audio/reminders toggles)
bot/redeem.py                  # Redemption codes: /redeem + /admin codes (self-contained; removal notes in docstring)
bot/reminders.py               # Daily reminder + fresh-mind XP bonus: settings flow, hourly job, rem: callbacks (self-contained; removal notes in docstring)
bot/sprint.py                  # Daily sprint challenge: 5× 90%+ fresh tests in 60 min, cosmetic results line (self-contained; removal notes in docstring)
bot/analytics.py               # Usage analytics: engaged training time (session duration_s) + raw interaction log (self-contained; removal notes in docstring)
bot/access.py                  # is_admin, admin_only decorator, subscription tier gating
bot/features.py                # Runtime feature flags (DB-persisted, cached in memory)
bot/audio_viz.py               # Audio Visualization flow (own module = clean removal)
bot/menu.py                    # Telegram command menu sync (startup + xp toggle; /admin only for admins)
gamification/achievements.py   # Achievement definitions + evaluation (defs in code, unlocks in DB); register_achievements() for extension sets
gamification/xp.py             # XP/level system: skills, level curve, diminishing returns, streak bonus
gamification/audio_xp.py       # Audio XP + audio achievements: visualization bar, first-listen-only, daily cap (self-contained; removal notes in docstring)
exercises/base.py              # BaseExercise ABC — implement to add new exercises
exercises/registry.py          # Exercise registry
exercises/word_memorization.py # Full word memorization exercise logic
exercises/audio_visualization.py # Audio story library scan, file_id cache, keyboards
database/models.py             # User, ExerciseSession, UserAchievement, UserSkill, BotSetting, enums
database/repositories.py       # DB query layer — stats/leaderboard aggregate in SQL, not Python
database/connection.py         # Async engine, session factory, SQLite migrations + backfill
data/                          # concrete_nouns.json (beginner), nouns.json, verbs.json, adjectives.json
data/audio/{1min,3min,5min}/   # Audio stories: .mp3 + optional .json sidecar (title, quiz); rescanned live
scripts/make_story.py          # Story text → mp3 via edge-tts + sidecar; --batch scripts/queue renders many, auto-buckets by word count (~175 wpm); samples in scripts/samples/, full workflow in ADMIN_GUIDE
scripts/STORY_GUIDE.md         # Story + quiz writing rules: variety axes (POV, setting, fantastical allowed), anti-meta quiz templates — always follow when writing new stories
dashboard.py                   # Streamlit analytics dashboard — reads a read-only SNAPSHOT (default snapshot.db), never the live DB; time/score/engagement charts + member drilldown
requirements-dashboard.txt     # Dashboard-only deps (streamlit, plotly, pandas, numpy) — kept out of the bot's requirements.txt
scripts/backup_db.sh           # SQLite online backup (never cp — WAL mode), integrity-checks the copy, repoints latest.db, prunes past KEEP_DAYS; cron'd daily on the VPS
docs/DASHBOARD.md              # Dashboard build/deploy guide (the dashboard itself now lives in dashboard.py)
docs/ADMIN_GUIDE.md            # Admin cheat sheet: commands, XP tuning, VPS ops
```

## Environment Variables

```
TELEGRAM_BOT_TOKEN=    # required
DATABASE_URL=          # default: sqlite+aiosqlite:///./mental_training.db
BOT_NAME=              # default: MentalTrainingBot
MAX_WORD_PAIRS=        # default: 100
DEFAULT_WORD_PAIRS=    # default: 10
ADMIN_TELEGRAM_IDS=    # comma-separated Telegram IDs allowed to use /admin
```

Config loaded from `.env` via `config/settings.py`. Get singleton with `get_settings()`.

## Commands

| Command | Handler |
|---------|---------|
| `/start` | `start_command` |
| `/help` | `help_command` |
| `/stats` | `stats_command` |
| `/history` | `history_command` (last 10 tests) |
| `/level` | `level_command` (XP bars; hidden when XP disabled) |
| `/achievements` | `achievements_command` |
| `/leaderboard` | `leaderboard_command` (opt-in via inline button) |
| `/exercises` | `exercises_command` |
| `/settings` | `settings_command` (user prefs in `preferences` JSON: compact-results toggle, daily-reminder setup) |
| `/redeem` | `redeem_command` (activate a one-time access code → subscription tier) |
| `/admin` | `admin_command` (admin-only: overview / `users` / `time [days]` / `export` CSV / `grant <id> <tier> [days]` / `codes <n> <tier> <days\|lifetime>`, `codes list` / `xp on\|off` / `audio on\|off` / `audioquiz on\|off` / `audioxp on\|off` / `reminders on\|off` / `sprint on\|off` / `analytics on\|off`) |

All callbacks routed through `callback_handler` → `CALLBACK_ROUTES` dict keyed by callback-data prefix (`word_memo`, `audio_viz`, `lb`, `menu`, `settings`, `placement`, `rem`). Text messages during tests routed through `text_message_handler`; answer recording is serialized per user with an asyncio lock (needed under concurrent updates).

## Word Memorization Feature Set

- **Formats** (picked first in the menu): 🔗 Pairs (recall the partner word) | 📜 Word List (one ordered list; quiz walks the chain *sequentially* — each word shown, user recalls the next one, so every prompt doubles as the reveal of the previous answer; reverse quiz walks backwards with "before" questions; retry re-asks missed links in chain order — N words → N−1 adjacent-link questions, `pair_index` = link index, `direction` next/prev in quiz items/results). Stored in session `parameters["format"]`; missing = pairs (pre-feature rows). Personal bests are per-format; stats/leaderboard/XP/achievements count both as real tests. List study timer `SECONDS_PER_WORD` (3s/word) vs pairs `SECONDS_PER_PAIR` (5s/pair)
- **Modes**: Training (study only) | Test (study → timed disappear → quiz)
- **Level-up button** — on every results/training-completion screen: "⬆️ Level up: N pairs/words" instantly starts the next size up (`NEXT_COUNT`), same difficulty/speed/mode/format; hidden at 100
- **Difficulty**: Beginner (concrete everyday nouns from `concrete_nouns.json`, ~580 words, curated subset of nouns.json) | Intermediate (all nouns + verbs) | Advanced (+adjectives)
- **Pair/word counts**: 5, 10, 15, 20, 30, 50, 75, 100
- **Per-question timer** with auto-skip on timeout; 2s grace window credits late answers to the timed-out question (pairs format only — disabled for list, where the next prompt reveals the answer)
- **Fuzzy matching** via Levenshtein distance (≤2 edits, disabled for short words)
- **Results shown at end only** (no mid-test feedback); per-user **compact results** toggle via `/settings` (`compact_results` pref) — score + pairs only
- **Retry Mistakes** — re-quizzes wrong answers, merges with baseline for accurate score. Saved with `mode: "retry"` and **excluded** from stats/leaderboard/PB/achievements via `_IS_SCORED_TEST` in repositories (still in admin export with `mode` column). XP: first retry after a study phase earns subset XP, 2nd+ retries earn 0 (anti-farm)
- **Placement test** — level calibration for new users: `/start` shows "📏 Find your level" button when user has 0 scored tests; retake via `/settings`. One fixed round (10 pairs, intermediate, normal speed), saved with `mode: "placement"` — excluded like retry, no XP/PB/achievements (streak still counts). Score → recommendation (`get_placement_recommendation`: <50% beginner/5, <75% beginner/10, <90% intermediate/10, else advanced/10), stored in `preferences["placement"]`, one-tap apply button starts a real test with those settings
- **Reverse quiz** — flips prompt/answer columns (list format: flips question direction). First reverse after a study phase: `mode: "reverse"`, counts as a real test, ×0.5 XP. 2nd+ reverse on the same set: `mode: "reverse_extra"` — 0 XP (nudge line suggests fresh test), no PB/achievements, excluded from stats/leaderboard via `_IS_SCORED_TEST` (anti-farm: memorize once, reverse forever). Repeat counters `test_reverse_rounds`/`test_retry_rounds` in per-user state, reset on every fresh study phase
- **Speed mode** — normal (5s/pair) or fast (2.5s/pair) study time
- **Progressive difficulty suggestions** at ≥90% score
- **Personal best tracking** with notifications
- **Anti-repeat** — rolling 200-word recent-words window in user preferences
- **User answer deletion** during tests (anti-cheat) + bulk message cleanup at transitions
- **Daily streak tracking** — shown after every test + in `/stats`; resets on missed day
- **Daily sprint challenge** (`bot/sprint.py`) — cosmetic line on every fresh-test result: complete 5 tests at ≥90% inside one 60-min window (window opens on first qualifying test), done resets midnight UTC. First test needs ≥10 words and sets the anchor challenge rating (count × difficulty × speed) — later tests only tick at anchor CR or higher (no finishing on easier lists). Sub-90%/easier tests just don't count (no run reset); expired window lazily resets on next test. Only `mode: "test"` rounds evaluated (retry/reverse/placement can't tick). Progress in `preferences["sprint"]`; no XP/achievements (completion-XP hook noted in module docstring). Kill switch `/admin sprint on|off` (default ON)
- **Achievements** — checked after every test; unlock notifications in results message

## Audio Visualization Feature Set

- **Concept**: user listens to a narrated story (.mp3) and visualizes it; passive by design. Optional multiple-choice **detail quiz** afterwards as a proxy score.
- **Feature flags** (both default OFF, DB-persisted): `audio_viz_enabled` (`/admin audio on|off` — off hides the exercise everywhere, entry points answer "paused") and `audio_viz_quiz_enabled` (`/admin audioquiz on|off` — on offers "Quick test / Skip" buttons after listening, user chooses per session). Gating is generic: `BaseExercise.feature_flag` attr + `is_exercise_enabled()` in `bot/features.py`.
- **Content**: `data/audio/{1min,3min,5min}/<story>.mp3` + optional `<story>.json` sidecar (`title`, `questions: [{q, options, answer}]`). Library rescanned every session — drop a file in, it's live, no restart. Generator: `scripts/make_story.py` (edge-tts). Telegram `file_id`s cached in gitignored `data/audio/file_ids.json` — each file uploads once.
- **Flow**: length pick → audio sent + prompt message → user taps Done (or Quick test). Done asks a one-tap **focus check** ("how many times did your mind wander?" 0 / 1–2 / 3–5 / 6+) → saved in session `parameters["distractions"]`, session saved only on answer (no vividness question — deliberately dropped for now). Quiz deletes the audio message first (anti-cheat, answers from memory). Options shuffled per session with answer index remapped. Story audio message is deleted on Done/quiz/next-story (Telegram queues chat audios as playlist → autoplay; only ever one story audio in chat), message id persisted in `preferences["audio_last_msg_id"]` so restarts can't leak one.
- **Bookkeeping**: passive listen → `mode: "audio_listen"`, `completed=True`, no score, **no streak** (deliberate — passive). Quiz → `mode: "audio_quiz"`, scored, **streak counts**. Both excluded from stats/leaderboard/PB/word-memo achievements (`audio_quiz` in `_IS_SCORED_TEST` exclusion; listens have no score), both in `/admin export`. Anti-repeat: `audio_heard` list (last 100 story ids) in user preferences.
- **XP + audio achievements** (`gamification/audio_xp.py`, self-contained): separate **visualization** skill bar (registered into `SKILLS` at import — no xp.py edits). Anti-farm layers: replayed stories (already in `audio_heard`) earn 0 XP; passive listen = small fixed XP per bucket (5/12/20); quiz = real XP per bucket (15/30/45) × accuracy² (0 below 50%, ×1.2 perfect); hard 80 XP/day cap tracked in `preferences["audio_xp_day"]` (UTC). No hard-streak/CR mechanics. 3 achievements (First Listen, Story Collector ×10 distinct, Perfect Recall ×3) via `register_achievements()` — isinstance-guarded checks so word-memo evaluation never fires them; counters SQL-aggregated in `get_audio_achievement_stats`. Kill switch `/admin audioxp on|off` (default ON) + respects global `/admin xp` flag; achievements evaluated even with XP off (same policy as word memo).
- **Removal**: exercise is fully self-contained — `exercises/audio_visualization.py`, `bot/audio_viz.py`, `data/audio/` plus one-line touch points (registry, route+import, enum value, flags, admin subcommands, `_IS_SCORED_TEST` entry). See ADMIN_GUIDE "Removing the exercise".

## Access Codes & Daily Reminders

- **Redemption codes** (`bot/redeem.py`): one-time codes gate the bot to paid Skool members without any Skool API. `/admin codes <n> <tier> <days|lifetime>` generates a batch (`MTB-XXXX-XXXX`, no 0/O/1/I), `/redeem CODE` claims it (atomic `UPDATE ... WHERE redeemed_by IS NULL` — no double-redeem race) and sets `subscription_tier` + `subscription_expires_at` (`duration_days NULL` = lifetime = expiry NULL). `redeemed_by` = Telegram ID → the Skool↔Telegram data link. Tiers still not *enforced* anywhere — enforcement is a separate launch decision.
- **Daily reminder + fresh-mind bonus** (`bot/reminders.py`): opt-in via `/settings`, 2-step setup (current local hour → derive UTC offset, whole hours only; then reminder hour). Hourly `job_queue` sweep pings users due at that UTC hour **unless they already trained today**. Ping button `rem:go:<diff>:<count>:<fmt>:<speed>` one-taps a test with last-used settings (preset from latest session params; falls back to placement rec, then intermediate/10). Starting within 15 min of the ping → ×1.25 XP (`claim_fresh_mind_bonus`, once/day via `bonus_date` pref, persisted). Kill switch `/admin reminders on|off` (default ON; feature is per-user opt-in). Prefs schema + removal notes in the module docstring. Session `parameters` now also store `"speed"` (needed for the preset; older rows default to normal).

## Usage Analytics

- **Engaged training time** (`ExerciseSession.duration_s`, real column): monotonic-clock seconds from round start to results save. Word memo starts the clock at the study phase (memorizing is the work); retry/reverse rounds re-stamp (quiz time only); audio starts when the audio message lands. NULL when unknown — training-mode rows (no completion event), restarts mid-round, or over cap (`MAX_ROUND_SECONDS` 1h; audio ~2× story length + 5 min). Totals are a floor, never inflated. Always recorded, not flag-gated (one column on a row that gets written anyway). Read via `/admin time [days]` + a `duration_s` CSV column.
- **Raw interaction stream** (`activity_events`): one row per update via a `TypeHandler` in group -1 (`activity_tracker`), fire-and-forget insert, never blocks or raises into a handler. Stores `telegram_id` (no user lookup on the hot path), `kind` (command/callback/message) + callback prefix — **never message text**. Telegram exposes no app-open/idle signal, so time-in-bot only exists as a sessionization of this stream (`IDLE_GAP_MIN` 5 min) — approximate, admin-side only, never quoted to users as their training time. Kill switch `/admin analytics on|off` (default ON).
- Retention: unlimited; `ActivityEventRepository.purge_older_than(days)` exists but nothing calls it.
- **Cohort analysis** ships in `dashboard.py` (ROADMAP #10 L2): minutes per day/user/exercise, commitment-vs-improvement-rate scatter, attempts by list size (where members stall), engagement sessionized from `activity_events`, per-member drilldown. Accuracy aggregates re-implement `_IS_SCORED_TEST` in pandas — **change one and the other silently disagrees**.
- Still planned (`docs/ROADMAP.md` #10): L3 daily personal recommendation folded into the reminder ping (rules engine, not an LLM), L4 personal + community PNG share cards.
- Product rule agreed with the owner: users only ever see *training* time. Reconstructed "time in bot" is admin-side only, and the leaderboard stays on accuracy — time is context, never rank.

## Gamification

- **XP/levels**: `gamification/xp.py`. Skills = bars (`SKILLS` dict; only "mnemonics" live, fed by word-memo tests via `EXERCISE_SKILLS` map). Challenge rating = pairs × difficulty × speed multipliers; XP diminishes when CR < `expected_challenge(level)` (floor 15%) — forces harder tests to keep leveling. Accuracy gates (anti-farm): score <50% = 0 XP, accuracy scales XP with exponent 2.0. Consecutive at/above-level tests scored ≥70% stack +10% XP each (cap +50%); hard attempt alone isn't enough. Level curve `xp_for_next_level()`. All constants documented in `docs/ADMIN_GUIDE.md`. XP computed only from the current quiz round (retry-mistakes = subset only, no farming); repeat-round throttle: first reverse ×0.5, extra reverses / 2nd+ retries on the same set = 0 XP. Admin kill switch `/admin xp on|off` → `bot/features.py` flag (DB `bot_settings` + in-memory cache, loaded at startup; single-process assumption); when off, nothing is calculated or shown but data is kept.
- **Achievements**: definitions live in `gamification/achievements.py` (code, emoji, name, description, check lambda over `AchievementContext`). Adding one = append to `ACHIEVEMENTS` list, no migration; other modules add their own sets via `register_achievements()` (checks must isinstance-guard their context — audio does this in `gamification/audio_xp.py`). Unlocks stored in `user_achievements` (unique user+code). 21 total: 18 word-memo incl. tiered I/II/III sets (Flawless/Speedster/Advanced Ace, by pair count 10/30/50+) + 3 audio. Never checked on retry rounds. Never rename a `code`.
- **Leaderboard**: `/leaderboard`, ranked by average test score (min 3 tests), only `leaderboard_opt_in` users listed; join/leave via inline buttons.
- **Subscriptions**: `bot/access.py` has `has_tier(user, SubscriptionTier.X)` + expiry-aware `get_effective_tier`. Not enforced anywhere yet; grant manually via `/admin grant`.

## Database Models

**User**: `telegram_id` (BigInteger), `username`, `first_name`, `last_name`, `language_code`, `subscription_tier` (FREE/BASIC/PREMIUM) + `subscription_expires_at`, `preferences` (JSON), `current_streak`, `longest_streak`, `last_trained_date`, `leaderboard_opt_in`, timestamps

**ExerciseSession**: `user_id`, `exercise_type`, `difficulty`, `parameters` (JSON: count, mode), `score`, `max_score`, `completed`, `duration_s` (engaged training seconds, nullable), timestamps. **Scores live in the real columns** (SQL-aggregatable), not in the JSON — legacy JSON-score rows are backfilled at startup. Rows are written only at completion, so `started_at` ≈ `completed_at`; duration comes from `duration_s`, not their difference. Composite index `(user_id, started_at)`.

**ActivityEvent**: `telegram_id`, `ts`, `kind`, `detail`; index `(telegram_id, ts)`. Raw interaction stream behind usage analytics (`bot/analytics.py`).

**UserAchievement**: `user_id`, `code`, `unlocked_at`; unique `(user_id, code)`.

**UserSkill**: `user_id`, `skill`, `xp`, `level`, `hard_streak`; unique `(user_id, skill)`. One row per XP bar per user — future bars need no schema change.

**BotSetting**: key-value store for runtime flags (`xp_enabled`, `audio_viz_enabled`, `audio_viz_quiz_enabled`, `audio_xp_enabled`, `reminders_enabled`, `sprint_enabled`, `analytics_enabled`).

**RedemptionCode**: `code` (unique), `tier`, `duration_days` (NULL = lifetime), `redeemed_by` (Telegram ID, NULL = unredeemed), `redeemed_at`, `created_at`.

Gotchas:
- SQLite migrations are hand-rolled in `connection.py::_run_sqlite_migrations` as a `{table: {column: ddl}}` map (create_all never alters existing tables). **Adding a column to a model means adding it there too** — the streak columns were once added without it, and every database created before that release crashed on every user query until it was noticed months later. Move to Alembic with the Postgres migration.
- JSON columns: always assign a **new** dict (`user.preferences = {**old, **new}`) — in-place mutation is silently not persisted (identity-based change detection).

## Adding a New Exercise

1. Create `exercises/your_exercise.py` extending `BaseExercise`
2. Implement: `get_difficulty_keyboard()`, `get_parameter_keyboard()`, `generate()`, `get_intro_message()`
3. Set class attrs: `name`, `description`, `exercise_type`
4. Register in `exercises/registry.py`
5. Add `ExerciseType` enum value in `database/models.py`
6. Add a callback route: `CALLBACK_ROUTES["your_type"] = your_callback_handler` in `bot/handlers.py` (flow handler in its own `bot/your_exercise.py`, like `bot/word_memo.py` / `bot/audio_viz.py`)
7. To reuse the quiz engine (`bot/quiz_engine.py`: per-question timers, grace window, retry/reverse), set the same `test_*` state keys plus `test_exercise_type`, provide `format_test_prompt`, `get_skip_keyboard`, `format_test_results`, `get_results_keyboard` on the exercise, and add the registry key → enum mapping to `ENGINE_EXERCISE_ENUM`
8. Map it to an XP bar: add the ExerciseType value to `EXERCISE_SKILLS` in `gamification/xp.py` (new bar = new `SkillDef` in `SKILLS`)

## Admin & Analytics

- `/admin` — totals, active users, tests, avg score
- `/admin users` — per-user progress (tests, avg/best %, streak, last active)
- `/admin export` — CSV of all scored sessions (opens in Excel/Sheets)
- `/admin time [days]` — engaged training minutes per user
- Web dashboard: `streamlit run dashboard.py` against a snapshot (`scp root@<VPS>:/root/backups/latest.db snapshot.db`). Every chart exports PNG. Deploy notes in `docs/DASHBOARD.md`
- Backups: `scripts/backup_db.sh`, cron'd daily at 03:00 UTC on the VPS, 14-day retention. **Never back up SQLite with `cp`** — WAL mode means a live copy can be inconsistent

## Deployment (VPS)

Deployed on an Ubuntu VPS (`root@<VPS_IP>` — real address kept out of this
public repo; it's in your `.env`/SSH config and the deploy notes you keep
locally).

```
/root/mental_training_bot/
├── venv/          # Python 3.12 venv — use venv/bin/python3 NOT system python3
└── ...
```

Systemd service: `/etc/systemd/system/mental_training_bot.service`
- `ExecStart=/root/mental_training_bot/venv/bin/python3 main.py`
- `Restart=always`, `RestartSec=5`

Key commands:
```bash
systemctl status mental_training_bot
systemctl restart mental_training_bot
journalctl -u mental_training_bot -n 50 --no-pager
```

## Known Issues / Future Work

- **Feature backlog lives in `docs/ROADMAP.md`** — agreed ideas with direction + build order (delayed recall/spaced repetition for both exercises incl. XP hooks, vividness rating, story-method exercise, visualization XP bar, daily community story, detail-density difficulty). Check it before proposing new features.
- **SQLite → PostgreSQL + Redis** migration planned; switch hand-rolled migrations to Alembic then. Full scaling plan with per-step triggers: `docs/SCALING.md`
- Subscription tiers exist + gating helpers ready, but no feature is gated and no payment flow
- `ExerciseType` enum has placeholder entries (NUMBER_SEQUENCE, PATTERN_RECOGNITION, MENTAL_MATH) — not implemented
- Quiz flow assumes private chats (`chat_id == telegram user id` in results/streak paths)


Read LEARNING.md at the start of every session and follow its instructions.
