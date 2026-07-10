# Feature Roadmap

Backlog of agreed-on ideas, roughly ordered by (training value × low effort).
Working agreement: build one at a time, flag-gate anything user-facing, keep
each feature removable like audio-viz. Details get worked out at build time —
what's written here is direction, not final spec.

Suggested build order: #2 → #4 → #1, with #3 as the next big exercise.

---

## 1. Delayed recall (spaced repetition) — flagship feature

Re-test users on material from previous days. Retrieval after sleep is where
consolidation actually happens — immediate quizzes test attention, delayed
ones test whether it stuck. Applies to BOTH exercises:

**Audio visualization**: next day the bot messages "Yesterday you heard *The
Night Train* — what color was the conductor's uniform?" Sessions already
store story id + timestamp. Sidecar questions beyond the 3 asked immediately
(`MAX_QUIZ_QUESTIONS`) can be reserved for the delayed round, or the same 3
reused with reshuffled options.

**Word memorization**: after a scored test, save the actual word pairs (add
`pairs` to session `parameters` JSON at save time — they currently live only
in handler state and are lost). A daily job then re-quizzes a sample (e.g.
5–10 pairs) from yesterday's test. Saved as `mode: "recall"`. Later: proper
SM-2-style intervals (1 → 3 → 7 days) per list.

**Shared plumbing** (build once, both exercises use it):
- Daily `job_queue` job scanning for due re-tests; per-user opt-in via
  `/settings` + a quiet-hours window; unanswered prompts expire silently.
- XP hook: delayed recall feeds the SAME skill bar as the source exercise
  (mnemonics / visualization) with a retention multiplier (e.g. 1.5×) —
  spaced retrieval is harder than immediate, should pay more.
- Streak counts (it's active work). Achievements later ("Iron Memory:
  90%+ on a 3-day-old list").
- Excluded from leaderboard averages initially (same `_IS_SCORED_TEST`
  pattern as retry/placement/audio_quiz).

## 2. Vividness self-rating after passive listens — build first

One tap after each listen: "How vivid was your movie? 1–5". Stored in session
`parameters` (`vividness`). Gives the passive exercise a visible progress
curve — `/stats` line like "avg vividness this month: 4.1 (was 2.8)". Very
low effort, big retention payoff.

## 3. "Story method" bridge exercise — the technique teacher

Third exercise/mode that closes the loop between the two existing ones:
show 10 words, explicitly instruct "link them into one absurd story in your
head", then quiz. Then show the user their story-method score vs their raw
word-memo baseline — the moment they *feel* the technique working. Reuses the
whole existing quiz engine (timers, retry, results); mostly new copy + intro
flow, not new machinery. This is the product's core promise made tangible.

## 4. Visualization XP bar + audio achievements — near-free

- `SkillDef("visualization")` in `SKILLS`, map `AUDIO_VISUALIZATION` in
  `EXERCISE_SKILLS` (gamification/xp.py). XP scaled by story length bucket +
  quiz score; small fixed XP for passive listens (or none — decide at build).
- Achievements: "First Listen", "Story Collector (10 stories)", "Perfect
  Recall ×3". Append to `ACHIEVEMENTS`, no migration.

## 5. Daily community story — Skool engagement

Everyone gets the same story on the same day; quiz scores feed a daily
mini-leaderboard. Shared experience → discussion in the community → the bot
markets itself inside the group. Needs: a "story of the day" picker (seeded
by date), a daily board query, maybe an announcement message.

## 6. Detail-density difficulty for audio

Same length buckets, but "advanced" stories pack ~2× the concrete details and
quizzes pull harder questions. Gives audio a progression ladder like
word-memo's difficulty tiers. Mostly content work (story writing guidelines
per tier in ADMIN_GUIDE), light code (difficulty field in sidecar + keyboard).
