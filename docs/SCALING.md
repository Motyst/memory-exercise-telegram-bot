# Scaling Notes

Audit date: 2026-07-09. Verdict: no changes needed at current scale. This file lists
what to change **when growth demands it**, in order, with the trigger for each step.

## Current capacity picture

- Single async process, `concurrent_updates(True)` — concurrency, not parallelism (one core).
- Code audit: no blocking calls in the request path. Word files load once at startup
  (`exercises/word_memorization.py::_load_words`); admin CSV export is sync but rare and
  admin-only. All handlers async, all DB via aiosqlite.
- Thousands of *registered* users ≈ 2–5% concurrent. A modest VPS handles low hundreds
  of concurrent quizzes fine. Memory per active session is trivial.
- Real bottleneck: **SQLite single-writer**. WAL allows concurrent reads during a write,
  but writes serialize. Many tests finishing simultaneously → write queue latency.

## What to watch (do this now, costs nothing)

```bash
journalctl -u mental_training_bot -n 200 --no-pager | grep -i "locked\|timeout\|slow"
```

First appearance of `database is locked` or noticeable reply lag under load = trigger
for step 1. Don't migrate before that — premature migration is risk with zero payoff.

## Step 1 — PostgreSQL migration (the main move)

**Trigger:** SQLite lock warnings, or sustained ~200+ concurrent active users.

- Swap `DATABASE_URL` to `postgresql+asyncpg://...` (add `asyncpg` dependency).
- Replace hand-rolled migrations in `database/connection.py::_run_sqlite_migrations`
  with Alembic (already noted in CLAUDE.md). The JSON-score backfill logic can be
  dropped once data is migrated.
- Data migration: dump SQLite → load Postgres (small DB, a one-off script is fine).
- SQLAlchemy 2.0 abstracts the rest; repositories should work unchanged. Verify JSON
  column behavior (`preferences`, `parameters`) — SQLite stores TEXT, Postgres JSONB.
- Keep WAL-mode pragmas conditional on the sqlite driver (they already are).

## Step 2 — Redis (only with Postgres, only if needed)

**Trigger:** feature flags / quiz state need to survive restarts, or multi-process.

- Current in-memory caches assume single process (`bot/features.py` flag cache,
  per-user asyncio answer locks, quiz state in `context.user_data`).
- Redis replaces those only if moving to multiple bot processes/webhooks. Not before.

## Step 3 — Webhooks instead of polling

**Trigger:** only if update latency matters at high volume, or Telegram rate concerns.

- Polling scales further than people expect; thousands of users is fine on polling.
- Webhook requires public HTTPS endpoint (reverse proxy + cert on the VPS). Low value
  until multi-process, so do after step 2 if at all.

## Step 4 — Vertical resources (last resort)

**Trigger:** event loop saturated (CPU pegged at ~100% on the bot process) after
steps 1–3, or memory pressure from genuinely huge concurrency.

- Bot work per user is trivial (word sampling, Levenshtein on short words, XP math);
  CPU saturation is unlikely before ~1000s of truly concurrent sessions.
- If it happens: bigger VPS first; multiple processes behind webhooks second (needs
  step 2, since in-memory state must move out of the process).

## Known non-issues (checked, don't "fix")

- Word list JSON loading — startup-only, cached.
- CSV export sync write — admin-only, milliseconds; revisit only past ~100k sessions.
- Per-user answer lock — in-memory asyncio lock, correct for single process.
