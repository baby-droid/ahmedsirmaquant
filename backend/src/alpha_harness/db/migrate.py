"""Bringing an existing database up to the current models.

``create_all`` creates missing *tables*. It does not touch a table that already exists,
so a column added to a model after the database was created is simply never created —
and the failure arrives much later, as ``no such column`` from whichever query happens
to select it first. That is a bad way to find out.

This module closes that gap. On every startup the live schema is compared against
``Base.metadata`` and missing columns and indexes are added.

**Additive only, and it checks.** The policy in :mod:`.models` is that nothing is ever
dropped or renamed — losing a row in ``simulation_record`` means losing the ability to
cancel a running simulation. So a column that has *disappeared* from a model is not
quietly reconciled; it is reported, because that is a change this migrator is
deliberately not able to make safely and a real migration tool is then wanted.

Why not Alembic: it solves coordination between environments and people, and there is
one database on one machine here. What this application needs is that an existing
database never falls behind its models without saying so, which is about sixty lines.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import Index, Table, text
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import Base

log = structlog.get_logger(__name__)


class MigrationError(RuntimeError):
    """A schema change this migrator will not make on its own."""


async def migrate(connection: AsyncConnection) -> dict[str, Any]:
    """Create what is missing and report what was done."""
    await connection.run_sync(Base.metadata.create_all)

    added_columns: list[str] = []
    added_indexes: list[str] = []
    unknown_columns: list[str] = []

    live_tables = set(await connection.run_sync(_table_names))

    for table in Base.metadata.sorted_tables:
        if table.name not in live_tables:
            continue  # create_all just made it, so it is current by construction.

        present = await _columns(connection, table.name)

        for column in table.columns:
            if column.name in present:
                continue
            ddl = _add_column_sql(table, column)
            await connection.execute(text(ddl))
            added_columns.append(f"{table.name}.{column.name}")

        expected = {c.name for c in table.columns}
        unknown_columns.extend(f"{table.name}.{name}" for name in sorted(present - expected))

        for index in table.indexes:
            if await _create_index(connection, index):
                added_indexes.append(index.name or "")

    if added_columns or added_indexes:
        log.info("db.migrated", columns=added_columns, indexes=added_indexes)
    if unknown_columns:
        # Not fatal: an extra column costs nothing at runtime and dropping it is exactly
        # the destructive act this migrator refuses to perform. But it means the model
        # and the database disagree, and that is worth saying out loud once per start.
        log.warning(
            "db.unknown_columns",
            columns=unknown_columns,
            detail=(
                "These exist in the database but not in the models. Nothing reads them. "
                "Removing them needs a real migration tool."
            ),
        )

    return {
        "columnsAdded": added_columns,
        "indexesAdded": added_indexes,
        "unknownColumns": unknown_columns,
    }


def _table_names(sync_connection: Any) -> list[str]:
    from sqlalchemy import inspect

    return list(inspect(sync_connection).get_table_names())


async def _columns(connection: AsyncConnection, table: str) -> set[str]:
    result = await connection.execute(text(f'PRAGMA table_info("{table}")'))
    return {row[1] for row in result.fetchall()}


async def _create_index(connection: AsyncConnection, index: Index) -> bool:
    """Create an index if it is missing. Returns whether it was created."""
    existing = await connection.execute(
        text("SELECT name FROM sqlite_master WHERE type = 'index' AND name = :name"),
        {"name": index.name},
    )
    if existing.first() is not None:
        return False
    await connection.run_sync(index.create)
    return True


def _add_column_sql(table: Table, column: Any) -> str:
    """``ALTER TABLE ... ADD COLUMN`` for one column.

    SQLite will not add a ``NOT NULL`` column to a table that already has rows unless a
    constant default comes with it, so one is derived from the model's own default.
    Where that is impossible the migration stops with an explanation rather than
    guessing a value into every existing row.
    """
    from sqlalchemy.dialects import sqlite

    rendered = column.type.compile(dialect=sqlite.dialect())
    ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {rendered}'

    if column.nullable:
        return ddl

    default = _literal_default(column)
    if default is None:
        raise MigrationError(
            f"Cannot add {table.name}.{column.name}: it is NOT NULL but has no constant "
            "default, so existing rows have no value to take. Give the column a default, "
            "make it nullable, or write a real migration."
        )
    return f"{ddl} NOT NULL DEFAULT {default}"


def _literal_default(column: Any) -> str | None:
    """A SQL literal for the column's default, or ``None`` if there is not one."""
    if column.server_default is not None:
        arg = getattr(column.server_default, "arg", None)
        return str(arg) if arg is not None else None

    default = column.default
    if default is None:
        return None

    arg = getattr(default, "arg", None)
    if callable(arg):
        return _callable_default(column, arg)
    return _literal(arg)


def _callable_default(column: Any, factory: Any) -> str | None:
    """A literal for a callable default, decided by the column's *type*.

    An earlier version assumed any callable default was a timestamp, which is true of
    ``utcnow`` and false of ``default=list`` on a JSON column — and SQLite rejects
    ``JSON NOT NULL DEFAULT CURRENT_TIMESTAMP`` outright, so the mistake took the whole
    application down on startup rather than producing a wrong value.
    """
    from sqlalchemy import JSON, Date, DateTime

    if isinstance(column.type, DateTime | Date):
        # Existing rows predate the column, so "now" is as honest as anything available.
        return "CURRENT_TIMESTAMP"

    # A factory such as ``list`` or ``dict`` produces the empty value the model itself
    # would have used. SQLAlchemy wraps callable defaults so they take an execution
    # context, so both shapes are tried; a factory that genuinely needs the context is
    # not a constant and cannot become one.
    produced: Any
    try:
        produced = factory(None)
    except TypeError:
        try:
            produced = factory()
        except TypeError:
            return None
    except Exception:
        return None

    if isinstance(column.type, JSON):
        import json

        return _quote(json.dumps(produced))
    return _literal(produced)


def _literal(value: Any) -> str | None:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, list | tuple | dict):
        import json

        return _quote(json.dumps(list(value) if isinstance(value, tuple) else value))
    return None


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
