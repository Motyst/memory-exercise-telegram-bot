# Admin Cheat Sheet

Personal reference for running the Mental Training Bot. Everything you can do
as admin, in one place.

---

## Your admin commands (in Telegram)

Your Telegram ID must be in `ADMIN_TELEGRAM_IDS` in the `.env` on the VPS.
Non-admins get silence — the commands don't exist for them.

| Command | What it does |
|---|---|
| `/admin` | Overview: users, actives, tests, avg score, XP system status |
| `/admin users` | Per-user progress: tests, avg %, best %, streak, last active (top 30 by activity) |
| `/admin export` | Sends you a CSV of every scored test — opens in Excel/Google Sheets |
| `/admin grant <telegram_id> <free\|basic\|premium> [days]` | Set a user's subscription tier; omit days = no expiry |
| `/admin xp on` / `/admin xp off` | Turn the whole XP/level system on/off (see below) |

Get someone's Telegram ID: it's in `/admin users` next to their name, or they
can message @userinfobot.

## XP system kill switch

`/admin xp off`:
- No XP calculated or stored for any test
- XP lines vanish from test results, `/stats`, `/help`
- `/level` replies "not available right now"
- **Existing XP is kept** — nothing is deleted

`/admin xp on` brings it all back instantly. No restart needed, survives
restarts (stored in DB `bot_settings` table).

## How XP works (for tuning)

All knobs in `gamification/xp.py`, top of file:

| Constant | Current | Meaning |
|---|---|---|
| `BASE_XP_PER_PAIR` | 4.0 | Raw XP per word pair |
| `DIFFICULTY_MULT` | 1.0 / 1.25 / 1.5 | beginner / intermediate / advanced |
| `SPEED_MULT_FAST` | 1.3 | Speed mode bonus |
| `ACCURACY_EXPONENT` | 1.5 | Higher = accuracy matters more |
| `PERFECT_BONUS` | 1.2 | Extra 20% for a 100% test |
| `EFFICIENCY_FLOOR` | 0.15 | Outgrown exercises never drop below 15% XP |
| `HARD_STREAK_BONUS_PER` | 0.10 | +10% per consecutive hard test |
| `HARD_STREAK_BONUS_CAP` | 0.50 | Streak bonus max +50% |

**Diminishing returns**: each level has an "expected challenge"
(`expected_challenge()` = 5 + 2.5/level). Tests below it earn proportionally
less. Tests at/above it are "hard" — full XP + they build the streak bonus.
So players must raise pairs / difficulty / speed to keep leveling.

**Level curve**: `xp_for_next_level()` = 80 + 45·(level−1)^1.3.
Reference (90% score): level 2 after ~2 easy tests; a 30-pair advanced test
gives 154 XP; a 100-pair advanced speed test gives 666 XP.

Retries only give XP for the retried questions — no farming full-test XP by
redoing mistakes. Reverse quiz counts as a full test.

**Round types**: every quiz round is saved with a `mode` — `test`, `reverse`
or `retry`. Reverse quizzes count as real tests everywhere. Retry rounds are
**excluded** from stats, leaderboard, personal bests and achievements (they're
practice on a subset), but still appear in `/admin export` with their mode so
you can see them in the CSV.

After editing constants: `systemctl restart mental_training_bot`. Existing
XP totals stay; only future gains change.

**Adding a future skill bar**: add a `SkillDef` to `SKILLS` and map the new
exercise in `EXERCISE_SKILLS` (both in `gamification/xp.py`). Each user gets
the new bar automatically on first XP gain.

## Achievements

Definitions: `gamification/achievements.py`. Add one = append to the
`ACHIEVEMENTS` list (code, emoji, name, description, check lambda) + restart.
No DB change needed. Never reuse/rename a `code` — unlocks are stored by code
(display names/descriptions can change freely).

Tiered achievements (I/II/III by pair count, 10/30/50+): Flawless, Speedster,
Advanced Ace. 18 total. Achievements are never checked on retry rounds.

## Users & data

- User progress: `/admin users`, deeper analysis via `/admin export` CSV
- Web dashboard with shareable PNG charts: build guide in `docs/DASHBOARD.md`
- Leaderboard is opt-in — users join via button under `/leaderboard`

## VPS operations

```bash
ssh root@<VPS_IP>

systemctl status mental_training_bot          # is it running?
systemctl restart mental_training_bot         # after any code/config change
journalctl -u mental_training_bot -n 50 --no-pager   # recent logs

# deploy latest code (normal path — commit + push locally first)
cd /root/mental_training_bot && git pull && systemctl restart mental_training_bot

# watch logs live while testing a command in Telegram
journalctl -u mental_training_bot -n 0 -f

# backup the database (do this before risky changes!)
cp /root/mental_training_bot/mental_training.db /root/backup_$(date +%F).db
```

Normal deploy flow is always: edit on laptop → commit → push to GitHub → `git pull`
on VPS → restart. Avoid editing files directly on the VPS — those edits sit
uncommitted and cause `git pull` merge conflicts later (local changes would be
overwritten). If a stray one-off edit did happen on the VPS and you're sure
GitHub's version supersedes it: `git checkout -- <file>` before pulling.

One-off single-file push without a full deploy cycle (e.g. testing a tiny
change before committing) — from a local Git Bash / PowerShell terminal:

```bash
scp bot/handlers.py root@<VPS_IP>:/root/mental_training_bot/bot/handlers.py
ssh root@<VPS_IP> "systemctl restart mental_training_bot"
```

Still commit + push the same change on the laptop afterward, or the VPS
diverges from GitHub again.

`.env` on the VPS needs:

```
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_IDS=<your telegram id>    # comma-separated if several admins
```

Add/edit a value without opening an editor:

```bash
echo 'ADMIN_TELEGRAM_IDS=123456789' >> /root/mental_training_bot/.env
systemctl restart mental_training_bot   # required to pick up .env changes
```

DB schema changes apply automatically at startup (migrations + backfills run
in `database/connection.py::init_db`).

## Quick diagnosis

| Symptom | Check |
|---|---|
| Bot silent | `systemctl status mental_training_bot`, then journalctl logs |
| `/admin` does nothing | Your ID missing from `ADMIN_TELEGRAM_IDS`, or no restart after adding it |
| Bot slow for everyone | Should not happen (concurrent updates on) — check VPS CPU/RAM: `htop` |
| "Conflict: terminated by other getUpdates" in logs | Two bot instances running — make sure it runs only on the VPS |
