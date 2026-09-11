"""Async SQLAlchemy engine and session factory.

WAL mode plus a busy timeout: the background simulation tracker writes while HTTP
handlers read, and SQLite's default rollback journal would make them block each other.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _apply_pragmas(dbapi_connection: object, _record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self._engine: AsyncEngine = create_async_engine(url, echo=echo, future=True)
        event.listen(self._engine.sync_engine, "connect", _apply_pragmas)
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )

    @classmethod
    def for_path(cls, path: Path, *, echo: bool = False) -> Database:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return cls(f"sqlite+aiosqlite:///{path}", echo=echo)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def create_all(self) -> dict[str, Any]:
        """Bring the schema up to the models. Additive only — never drops.

        Not just ``create_all``: that creates missing *tables* and leaves an existing
        table alone, so a column added to a model later never appears and the first
        query to select it fails with ``no such column``. See :mod:`.migrate`.
        """
        from .migrate import migrate

        async with self._engine.begin() as conn:
            return await migrate(conn)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A session that commits on success and rolls back on failure."""
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def healthcheck(self) -> bool:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            return result.scalar() == 1

    async def dispose(self) -> None:
        await self._engine.dispose()


__all__ = ["AsyncSession", "Database"]
