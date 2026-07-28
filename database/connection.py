"""
Async database connection and session management.
"""

import logging

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import event, select
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from config import get_settings
from .models import Base, ExerciseSession

logger = logging.getLogger(__name__)

settings = get_settings()

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True
)

# Enable WAL mode for better concurrent write performance.
# PRAGMA is SQLite-only — guard so a future Postgres/MySQL DATABASE_URL
# doesn't blow up on first connect.
if engine.dialect.name == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def set_wal_mode(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

# Create async session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def _run_sqlite_migrations(conn) -> None:
    """Idempotent in-place migrations for existing SQLite databases.

    create_all only creates missing tables — it never alters existing ones,
    so new columns/indexes must be added here. Replace with Alembic on the
    Postgres migration.
    """
    # column name -> the type/default clause ALTER TABLE needs. Adding a
    # column to a model means adding it here too, or databases created before
    # the change keep failing every query that selects it.
    added_columns = {
        "users": {
            "leaderboard_opt_in": "BOOLEAN NOT NULL DEFAULT 0",
            "current_streak": "INTEGER DEFAULT 0",
            "longest_streak": "INTEGER DEFAULT 0",
            "last_trained_date": "DATE",
        },
        "exercise_sessions": {
            "duration_s": "INTEGER",
        },
    }
    for table, columns in added_columns.items():
        result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        existing = {row[1] for row in result.fetchall()}
        if not existing:  # table doesn't exist yet — create_all just made it
            continue
        for name, ddl in columns.items():
            if name not in existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                )
                logger.info(f"Migration: added {table}.{name}")

    await conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_sessions_user_started "
        "ON exercise_sessions (user_id, started_at)"
    )


async def _backfill_session_scores() -> None:
    """One-time backfill: older sessions stored score/max_score only inside the
    parameters JSON blob. Copy them into the real columns so SQL aggregation
    (stats, leaderboard, admin) sees historical data."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ExerciseSession).where(ExerciseSession.max_score.is_(None))
        )
        migrated = 0
        for sess in result.scalars():
            params = sess.parameters or {}
            if params.get("mode") == "test" and params.get("max_score"):
                sess.score = params.get("score", 0)
                sess.max_score = params["max_score"]
                sess.completed = True
                sess.completed_at = sess.completed_at or sess.started_at
                migrated += 1
        if migrated:
            await session.commit()
            logger.info(f"Migration: backfilled score columns for {migrated} sessions")


async def init_db():
    """Initialize database tables and run lightweight migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await _run_sqlite_migrations(conn)
    await _backfill_session_scores()


async def close_db():
    """Close database connections."""
    await engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
