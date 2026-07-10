# Story Writing Guide — Audio Visualization

Rules for generating new story batches (for the AI writing them and the human
reviewing them). Goal: every story should feel different from the last one the
user heard, and the quiz must not be gameable by a meta-strategy.

Word targets at the default voice/pace (~175 spoken wpm):
`1min` ≈ 175 words · `3min` ≈ 525 · `5min` ≈ 875.

## Why this guide exists

The first batch (10 stories) converged on one formula: second-person "Picture
a..." opening, calm pastoral scene at dawn, an elderly figure, a bystander
animal, a quiet poetic closing image — and quizzes that only ever asked
"what color / how many / which animal". Users who take a few quizzes learn to
memorize colors and numbers and ignore the actual scene, which defeats the
visualization purpose. Everything below exists to prevent that regression.

## Rule zero: stay concrete and visual

The exercise trains *visualization*. Every paragraph should hand the listener
something to see: objects with textures and materials, colors, light, shapes,
movement, spatial arrangement. "A copper kettle, dented on one side, catching
the last orange light" — yes. "A feeling of quiet importance" — no, unless it
is carried by an image. Avoid abstraction that can't be pictured; it doesn't
hinder just the quiz, it hinders the training itself. Rich visual detail
(including colors and countable things) belongs in the *text* — the anti-meta
rules below are about what the *quiz* asks, not about draining the stories.
Fantastical stories must be just as concrete: a surreal image the listener can
actually render (rain falling upward in silver threads), not a vague concept.

## Vary these axes — every story in a batch should differ on most of them

- **Point of view**: second person ("you are..."), first person, a named
  character, an animal, even an object (a coin, a lighthouse, a cloud).
  At most half of any batch in second person.
- **Realism**: realistic scenes are fine, but fantastical is *encouraged* —
  floating islands, a city where it rains upward, talking whales, rooms bigger
  on the inside, dream logic. Impossible things are excellent visualization
  material and add natural randomness. Don't feel bound by the real world.
- **Setting**: rotate hard. Check `data/audio/` for what already exists before
  writing. Under-used so far: urban/night city, indoor-modern, workshop or
  machine rooms, underwater, space, jungle, festivals and crowds, abstract or
  surreal spaces.
- **Time of day**: not everything at dawn. Night, noon glare, twilight,
  timeless dream-time.
- **Tone and structure**: calm-pastoral is the house default — break it.
  Tension, comedy, mystery, wonder, mild eeriness. Not every story needs a
  quiet poetic final image.
- **Opening line**: never open two stories in a batch the same way. Ban on
  making "Picture a..." the default. Starting mid-action is good.

## Stock-pattern bans (learned from batch 1)

- No default elderly-figure character. No default bystander animal watching
  the scene. (Either is fine occasionally — not in every story.)
- Don't reuse imagery across stories (batch 1 had two pyramids of stacked
  goods, two "spilled X" similes, two white dogs, two lighthouses).
- Don't salt the text with countable numbers just to have quiz material.

## Quiz rules (3 questions per story — the bot asks the first 3 in the file)

- **Max ONE** classic "what color was X / how many N" question per story.
- Every question should be answerable by *replaying the mental image* — the
  test is of the scene the listener built, not of trivia. Mix these templates:
  - spatial — "where was X?", "what was next to X?"
  - sequence — "what happened right after X?"
  - action — "what did X do?", "how did X move?"
  - odd detail — "what was strange about X?"
  - appearance — "what did X look like?" (fuller than a single color)
  - sensory — "what did it smell/sound/feel like?" (sparingly; still imagery)
- Avoid questions about spoken lines or abstract meaning unless the line is
  itself a picture — quotes test listening, not visualization.
- The correct answer must be stated (or unmistakably shown) in the story text.
  Distractors should be plausible for someone who didn't listen, but clearly
  wrong for someone who visualized the scene.
- Options are shuffled at runtime, so the position of the correct answer in
  the file doesn't matter — putting it at index 0 is easiest to author.
- Sidecar format: `{"questions": [{"q": "...", "options": ["A","B","C","D"], "answer": 0}]}`.

## Pre-render checklist (before `make_story.py --batch`)

- [ ] No two stories in the batch share setting + POV + time of day
- [ ] Opening lines all differ; not all second person
- [ ] At least one story is fantastical / breaks real-world rules
- [ ] No elderly-figure or bystander-animal repeats across the batch
- [ ] Each quiz: ≤1 color/count question, 3 questions total, answers verified
      against the text
- [ ] Word counts within ~10% of the target bucket
