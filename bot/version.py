"""
Running-build info for `/admin version` and `/admin changes`.

Read from git ONCE, on first use after startup, and cached for the life of
the process: the answer must describe the code that is actually running,
not whatever `git pull` has since put on disk. A pull without a restart
therefore shows the OLD commit here — which is exactly the mismatch an
admin wants to catch.

Falls back to "unknown" when git or the .git directory is unavailable
(e.g. a tarball deploy). Self-contained: delete this module plus the two
`/admin` subcommands and the startup log line in bot/__init__.py.
"""

import logging
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import telegram

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DEPTH = 20          # commits cached for /admin changes
DEFAULT_CHANGES = 8         # shown by a bare /admin changes
_SEP = "\x1f"               # field separator that never appears in a subject


@dataclass(frozen=True)
class BuildInfo:
    short: str
    full: str
    subject: str
    date: str               # committer date, YYYY-MM-DD
    branch: str
    dirty: bool             # tracked files edited on disk (hand edits on the server)
    history: tuple[tuple[str, str, str], ...]   # (short, date, subject), newest first
    started_at: datetime    # process start, UTC


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True,
            timeout=5, check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"git {' '.join(args)} failed: {e}")
        return None


def _load() -> BuildInfo:
    started = datetime.now(timezone.utc)
    head = _git("log", "-1", f"--format=%h{_SEP}%H{_SEP}%cs{_SEP}%s")
    if not head:
        return BuildInfo(
            "unknown", "unknown", "git unavailable", "?", "?", False, (), started,
        )
    short, full, date, subject = head.split(_SEP)
    branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "?"
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no"))
    raw = _git("log", f"-{HISTORY_DEPTH}", f"--format=%h{_SEP}%cs{_SEP}%s") or ""
    history = tuple(
        tuple(line.split(_SEP, 2)) for line in raw.splitlines() if line  # type: ignore[misc]
    )
    return BuildInfo(short, full, subject, date, branch, dirty, history, started)


_info: BuildInfo | None = None


def get_build_info() -> BuildInfo:
    global _info
    if _info is None:
        _info = _load()
    return _info


def _uptime(started_at: datetime) -> str:
    seconds = int((datetime.now(timezone.utc) - started_at).total_seconds())
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_version() -> str:
    """Plain text (no Markdown — commit subjects contain `_` and `*`)."""
    b = get_build_info()
    tree = "⚠️ local edits on disk" if b.dirty else "clean"
    lines = [
        "🏷 Running build",
        f"Commit: {b.short} — {b.subject}",
        f"Date: {b.date} · branch {b.branch} · {tree}",
        f"Up since: {b.started_at.strftime('%Y-%m-%d %H:%M')} UTC ({_uptime(b.started_at)})",
        f"Python {platform.python_version()} · python-telegram-bot {telegram.__version__}",
    ]
    if b.dirty:
        lines.append(
            "\nLocal edits mean the server differs from git — "
            "the next `git pull` will refuse until they are stashed."
        )
    return "\n".join(lines)


def format_changes(n: int = DEFAULT_CHANGES) -> str:
    """Last *n* commits of the running build, newest first."""
    b = get_build_info()
    if not b.history:
        return "No git history available on this deploy."
    n = max(1, min(n, HISTORY_DEPTH))
    lines = [f"📝 Recent changes (last {n} of running build {b.short})\n"]
    for short, date, subject in b.history[:n]:
        lines.append(f"• {date}  {subject}  ({short})")
    if len(b.history) > n:
        lines.append(f"\n/admin changes {min(len(b.history), HISTORY_DEPTH)} for more")
    return "\n".join(lines)
