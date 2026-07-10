"""
Story-to-audio generator for the Audio Visualization exercise.

Turns a plain-text story into an .mp3 (via edge-tts, free Microsoft neural
voices) and drops it straight into data/audio/<bucket>/ where the bot picks
it up on the next session — no restart needed.

Setup (once):     pip install edge-tts
Usage:
    python scripts/make_story.py story.txt --bucket 3min
    python scripts/make_story.py story.txt --bucket 1min --title "The Old Pier" \
        --voice en-US-AndrewNeural --questions questions.json

- Output filename is a slug of the title (or the input filename).
- --questions copies a quiz sidecar next to the mp3. Format:
    {"questions": [{"q": "...", "options": ["A","B","C","D"], "answer": 0}]}
  The title is written into the sidecar too, so it shows nicely in Telegram.
- Voice ideas: en-US-AndrewNeural (calm male), en-US-AvaNeural (warm female),
  en-GB-RyanNeural (British male). Full list: edge-tts --list-voices

Rule of thumb for length: ~140 spoken words per minute.
1min ≈ 140 words · 3min ≈ 420 words · 5min ≈ 700 words.
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

AUDIO_DIR = Path(__file__).parent.parent / "data" / "audio"
BUCKETS = ("1min", "3min", "5min")
DEFAULT_VOICE = "en-US-AndrewNeural"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "story"


async def render(text: str, voice: str, out_path: Path) -> None:
    try:
        import edge_tts
    except ImportError:
        sys.exit("edge-tts is not installed. Run:  pip install edge-tts")
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_path))


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a story text to bot audio.")
    parser.add_argument("story_file", help="Plain-text file with the story")
    parser.add_argument("--bucket", choices=BUCKETS, required=True,
                        help="Length folder the story belongs in")
    parser.add_argument("--title", help="Display title (default: from filename)")
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--questions",
                        help="JSON file with quiz questions to copy as sidecar")
    args = parser.parse_args()

    story_path = Path(args.story_file)
    text = story_path.read_text(encoding="utf-8").strip()
    if not text:
        sys.exit(f"{story_path} is empty.")

    title = args.title or story_path.stem.replace("_", " ").replace("-", " ").title()
    stem = slugify(args.title) if args.title else slugify(story_path.stem)

    out_dir = AUDIO_DIR / args.bucket
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / f"{stem}.mp3"

    words = len(text.split())
    print(f"Rendering {words} words (~{words / 140:.1f} min) with {args.voice}...")
    asyncio.run(render(text, args.voice, mp3_path))
    print(f"OK: {mp3_path}")

    sidecar = {"title": title}
    if args.questions:
        qdata = json.loads(Path(args.questions).read_text(encoding="utf-8"))
        sidecar["questions"] = qdata.get("questions", qdata if isinstance(qdata, list) else [])
    sidecar_path = mp3_path.with_suffix(".json")
    sidecar_path.write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    print(f"OK: {sidecar_path}")
    print("\nDone. The bot picks this up automatically — no restart needed.")
    print("Deploy to VPS:  scp both files to /root/mental_training_bot/data/audio/"
          f"{args.bucket}/  (or commit + git pull)")


if __name__ == "__main__":
    main()
