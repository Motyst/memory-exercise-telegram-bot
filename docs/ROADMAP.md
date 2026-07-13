# Feature Roadmap

Backlog of agreed-on ideas, roughly ordered by (training value × low effort).
Working agreement: build one at a time, flag-gate anything user-facing, keep
each feature removable like audio-viz. Details get worked out at build time —
what's written here is direction, not final spec.

Suggested build order: #2 → #4 → #1 (#9's notification plumbing is now
live for #1 to ride on), with #3 as the next big exercise.
Shipped: #7 (redemption codes), #9 (daily reminder + fresh-mind bonus).

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

## 7. Redemption codes + founding-member offer — Skool access gate ✅ SHIPPED

One-time codes gate the bot to paid Skool members, no Skool API needed:

- New `redemption_codes` table: `code`, `tier`, `duration_days`
  (NULL = lifetime), `redeemed_by`, `redeemed_at`.
- `/admin codes <n> <tier> <days|lifetime>` generates a batch;
  `/redeem CODE` in the bot sets `subscription_tier` +
  `subscription_expires_at` (NULL = never expires). One-time codes
  (not multi-use) so a leaked code burns one spot, not the campaign.
- **Founding-member campaign**: first 100 signups get full access forever —
  generate the first batch as 100 lifetime codes, hand out per member in the
  paid Skool section. After those, switch to 30-day batches rotated monthly.
- Later: replace monthly rotation with Zapier "member joined/left" webhook →
  endpoint on the VPS → auto grant/revoke.

## 8. Milestone level trials + community rewards

XP levels stay automatic except every 5th level, which is gated by a
**trial**: a fixed challenge test (count/difficulty/speed defined per
milestone), pass at ≥90% to cross. Rare enough to feel like a boss fight.

- Pass → badge/title shown on leaderboard ("🏅 Adept", "🏆 Master").
- Skool rewards are manual (no Skool API for points), which keeps them
  personal: shoutout post on milestone, classroom module unlocks, higher
  milestones = free month / 1-on-1 call.
- Pairs well with #5 (daily community story) — trials as community events.

## 9. Daily training reminder + fresh-mind XP bonus — #1's plumbing ✅ SHIPPED

Opt-in daily notification with a one-tap preset test start; answering fast
pays bonus XP. Builds the daily `job_queue` sweep + opt-in + quiet-hours
plumbing that delayed recall (#1) needs — build this first, #1 rides on it.

- `/settings`: pick reminder hour + preferred test (difficulty/count/speed/
  format, default = last used). Off by default, one tap to disable, never
  ping twice a day. Unanswered prompts expire silently.
- Notification carries a one-tap button ("Start: Intermediate, 15 pairs")
  with the preset — the button IS the feature, no menu friction.
- **Fresh-mind bonus**: start within 15 min of the ping → ×1.25 XP on that
  test, once per day. Multiplier not flat XP (flat = farmable with tiny
  tests; multiplier scales with challenge rating like the rest of xp.py).
- **Streak-saver variant** (phase 2, maybe the better default): only ping
  when today's streak is about to die (e.g. 21:00 user time, no test yet).
  Loss aversion > reward.
- Once #1 exists, the same daily slot delivers recall prompts ("Quick one:
  5 pairs from yesterday") — one notification channel, two features.
- **Timezone catch**: Telegram exposes no user timezone. Setup asks "what
  time is it for you right now?" (hour buttons) → derive + store offset in
  prefs. Good enough until the Postgres era.
- Achievement later: "Early bird — 7 on-time responses in a row."

## Parked ideas

- **Study-phase metronome audio**: Telegram bots can't trigger vibration or
  sounds, so the workaround is a pre-generated tick mp3 (tick every
  5s/2.5s, duration = study time) sent with the study message; `file_id`
  cached like audio stories, opt-in via `/settings`. Parked — revisit if
  users ask for pacing help.
