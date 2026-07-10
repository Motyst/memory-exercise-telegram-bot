"""
Story-to-audio generator for the Audio Visualization exercise.

Turns plain-text stories into .mp3s (via edge-tts, free Microsoft neural
voices) and drops them straight into data/audio/<bucket>/ where the bot picks
them up on the next session — no restart needed.

Setup (once):     pip install edge-tts

Single story:
    python scripts/make_story.py story.txt
    python scripts/make_story.py story.txt --bucket 1min --title "The Old Pier" \
        --voice en-US-AndrewNeural --questions questions.json

Batch (the queue workflow — generate many stories in one go):
    1. Put story files in scripts/queue/:
           scripts/queue/the_old_pier.txt
           scripts/queue/the_old_pier.questions.json   # optional quiz sidecar
    2. Run:
           python scripts/make_story.py --batch scripts/queue
    3. Every .txt is rendered into the right data/audio/<bucket>/ folder;
       processed sources move to scripts/queue/done/.

- Bucket is chosen automatically from word count (closest of 1/3/5 min);
  --bucket overrides in single mode.
- Title comes from the filename (--title overrides in single mode).
- Questions sidecar format:
    {"questions": [{"q": "...", "options": ["A","B","C","D"], "answer": 0}]}
- Voice ideas: en-US-AndrewNeural (calm male), en-US-AvaNeural (warm female),
  en-GB-RyanNeural (British male). Full list: edge-tts --list-voices
- --rate slows/speeds speech ("-10%" default — measured comfortable pace).

Word targets (measured at the default voice + rate, ~175 spoken words/min):
    1min ≈ 175 words · 3min ≈ 525 words · 5min ≈ 875 words.
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
# edge-tts default pace measured ~192 wpm — noticeably fast for a
# close-your-eyes visualization exercise. -10% lands around 175 wpm.
DEFAULT_RATE = "-10%"
WORDS_PER_MIN = 175  # effective rate at DEFAULT_VOICE + DEFAULT_RATE
BUCKET_MINUTES = {"1min": 1, "3min": 3, "5min": 5}


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "story"


def choose_bucket(word_count: int) -> str:
    """Closest length bucket for a story of this many words."""
    minutes = word_count / WORDS_PER_MIN
    return min(BUCKET_MINUTES, key=lambda b: abs(BUCKET_MINUTES[b] - minutes))


async def render(text: str, voice: str, rate: str, out_path: Path) -> None:
    try:
        import edge_tts
    except ImportError:
        sys.exit("edge-tts is not installed. Run:  pip install edge-tts")
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(out_path))


def load_questions(path: Path) -> list:
    qdata = json.loads(path.read_text(encoding="utf-8"))
    return qdata.get("questions", qdata if isinstance(qdata, list) else [])


def process_story(
    story_path: Path, voice: str, rate: str,
    bucket: str | None = None, title: str | None = None,
    questions_path: Path | None = None,
) -> Path:
    """Render one story text into its bucket; returns the mp3 path."""
    text = story_path.read_text(encoding="utf-8").strip()
    if not text:
        sys.exit(f"{story_path} is empty.")

    words = len(text.split())
    bucket = bucket or choose_bucket(words)
    title = title or story_path.stem.replace("_", " ").replace("-", " ").title()
    stem = slugify(title)

    out_dir = AUDIO_DIR / bucket
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / f"{stem}.mp3"

    est_min = words / WORDS_PER_MIN
    print(f"[{story_path.name}] {words} words -> ~{est_min * 60:.0f}s "
          f"-> bucket {bucket} ({voice}, rate {rate})")
    asyncio.run(render(text, voice, rate, mp3_path))
    print(f"  OK: {mp3_path}")

    sidecar = {"title": title}
    if questions_path and questions_path.exists():
        sidecar["questions"] = load_questions(questions_path)
        print(f"  quiz: {len(sidecar['questions'])} questions")
    sidecar_path = mp3_path.with_suffix(".json")
    sidecar_path.write_text(
        json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    print(f"  OK: {sidecar_path}")
    return mp3_path


def run_batch(queue_dir: Path, voice: str, rate: str) -> None:
    txt_files = sorted(queue_dir.glob("*.txt"))
    if not txt_files:
        sys.exit(f"No .txt files in {queue_dir}.")
    done_dir = queue_dir / "done"
    done_dir.mkdir(exist_ok=True)

    for txt in txt_files:
        questions = txt.with_name(f"{txt.stem}.questions.json")
        process_story(txt, voice, rate,
                      questions_path=questions if questions.exists() else None)
        txt.rename(done_dir / txt.name)
        if questions.exists():
            questions.rename(done_dir / questions.name)

    print(f"\nDone: {len(txt_files)} stories rendered, sources moved to {done_dir}.")
    print("Deploy: commit + push + git pull on the VPS (or scp the new files "
          "into /root/mental_training_bot/data/audio/). No restart needed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render story texts to bot audio.")
    parser.add_argument("story_file", nargs="?",
                        help="Plain-text story file (single mode)")
    parser.add_argument("--batch", metavar="DIR",
                        help="Process every .txt in DIR (see module docstring)")
    parser.add_argument("--bucket", choices=BUCKETS,
                        help="Length folder override (default: auto from word count)")
    parser.add_argument("--title", help="Display title (default: from filename)")
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--rate", default=DEFAULT_RATE,
                        help='Speech rate, e.g. "-10%%" (default) or "+5%%"')
    parser.add_argument("--questions",
                        help="JSON file with quiz questions to copy as sidecar")
    args = parser.parse_args()

    if args.batch:
        run_batch(Path(args.batch), args.voice, args.rate)
        return
    if not args.story_file:
        parser.error("Provide a story file or --batch DIR.")

    process_story(
        Path(args.story_file), args.voice, args.rate,
        bucket=args.bucket, title=args.title,
        questions_path=Path(args.questions) if args.questions else None,
    )
    print("\nDone. The bot picks this up automatically — no restart needed.")


if __name__ == "__main__":
    main()
