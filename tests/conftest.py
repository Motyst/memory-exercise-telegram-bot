"""
Test bootstrap. Runs before any test module is imported, so the environment
is pinned here: a throwaway SQLite file for the database, a dummy token, and
a fixed admin id — nothing touches the developer's real .env or DB.
"""

import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="mtb-tests-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:test-token"
os.environ["ADMIN_TELEGRAM_IDS"] = "999"

import pytest_asyncio  # noqa: E402

from database import init_db  # noqa: E402

ADMIN_ID = 999


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _database():
    """Create the schema once; every test shares the file. Tests that write
    use their own telegram ids so they don't see each other's rows."""
    await init_db()


_next_id = [10_000]


def fresh_telegram_id() -> int:
    """Unique telegram id per call — isolation without truncating tables."""
    _next_id[0] += 1
    return _next_id[0]
