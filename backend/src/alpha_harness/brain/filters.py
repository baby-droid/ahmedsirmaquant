"""The alpha filter DSL.

Alpha listing does not use ordinary query parameters. The comparison operator is part of
the parameter *name*, so a filter is one opaque token::

    ?is.sharpe>=1.58&status!=UNSUBMITTED&name~momentum&settings.region=USA

Anything built with a normal dict-to-querystring helper produces
``is.sharpe=%3E%3D1.58`` instead, which the server reads as an equality test against the
literal string ``>=1.58`` and quietly returns nothing. Hence a module of its own.

Two behaviours of the platform's own client are mirrored deliberately, because not
mirroring them changes which alphas come back:

**``hidden`` defaults to false.** Omit it and hidden alphas are silently excluded. That
is fine as a default and wrong as a surprise, so :class:`AlphaQuery` states it.

**Alpha listing stops at an offset of 1,000.** Past it the platform answers "Cannot
display more than the first 1,000 alphas. Apply filters to narrow results and see more."
(checked live 2026-09-10), so a whole pool is read in windows bounded by ``dateCreated``.

**Date bounds are full timestamps, and a date's upper bound is inclusive.** The v4 list
refuses a bare date ("Expected ISO 8601 datetime with timezone"), so a datetime is sent
as an Eastern timestamp and a date as the start of its day in ``America/New_York`` — or
of the next day for ``dateCreated<``, so "created before the 5th" includes the 5th, as
the platform's own client does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

#: Where the platform's calendar lives, for both quota and date filters.
PLATFORM_TZ = ZoneInfo("America/New_York")

Operator = Literal["=", "!=", ">", ">=", "<", "<=", "~"]

#: Longest first, and the order matters: with ``=`` earlier in this tuple, ``is.sharpe>=1.25``
#: parses as the field ``is.sharpe>`` compared with ``=`` against ``1.25``, which is wrong
#: in a way that still produces a plausible-looking request.
OPERATORS: tuple[str, ...] = (">=", "<=", "!=", ">", "<", "~", "=")

#: The same set in the order a person would read them, for the UI.
OPERATOR_CHOICES: tuple[str, ...] = ("=", "!=", ">", ">=", "<", "<=", "~")


@dataclass(frozen=True, slots=True)
class Filter:
    """One comparison. ``field`` and ``op`` are concatenated on the wire."""

    field: str
    op: Operator
    value: Any

    def token(self) -> str:
        return f"{quote(self.field, safe='.')}{self.op}{_render(self.value)}"


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return quote(moment.astimezone(PLATFORM_TZ).isoformat(), safe="")
    if isinstance(value, date):
        return value.isoformat()
    return quote(str(value), safe="")


def parse(expression: str) -> Filter:
    """Parse ``is.sharpe>=1.25`` into a filter.

    Longest operators first, so ``>=`` is not read as ``>`` followed by a value of
    ``=1.25``.
    """
    for op in OPERATORS:
        index = expression.find(op)
        if index > 0:
            return Filter(expression[:index], op, expression[index + len(op) :])  # type: ignore[arg-type]
    raise ValueError(
        f"{expression!r} is not a filter. Write it as field, operator, value — for "
        "example is.sharpe>=1.25 or status!=UNSUBMITTED."
    )


@dataclass(slots=True)
class AlphaQuery:
    """A request for a page of alphas."""

    limit: int = 50
    offset: int = 0
    #: ``-dateCreated`` for newest first. The leading minus is the platform's own syntax.
    order: str = "-dateCreated"
    filters: list[Filter] | None = None

    #: Tri-state on purpose. ``False`` excludes hidden alphas, ``True`` returns only
    #: hidden ones, and ``None`` means "do not filter" — which the platform does *not*
    #: default to, so it has to be sent explicitly.
    hidden: bool | None = False

    #: Convenience bounds, normalised to the platform's calendar.
    created_after: date | datetime | None = None
    created_before: date | datetime | None = None

    def tokens(self) -> list[str]:
        tokens = [f"limit={self.limit}", f"offset={self.offset}"]
        if self.order:
            tokens.append(f"order={quote(self.order, safe='-.')}")

        for entry in self.filters or []:
            tokens.append(entry.token())

        if self.hidden is not None:
            tokens.append(f"hidden={'true' if self.hidden else 'false'}")

        if self.created_after is not None:
            tokens.append(
                Filter("dateCreated", ">", _bound(self.created_after, next_day=False)).token()
            )
        if self.created_before is not None:
            tokens.append(
                Filter("dateCreated", "<", _bound(self.created_before, next_day=True)).token()
            )

        return tokens

    def query(self) -> str:
        return "&".join(self.tokens())

    def path(self, user_id: str = "self") -> str:
        return f"/users/{user_id}/alphas?{self.query()}"


def _bound(value: date | datetime, *, next_day: bool) -> datetime:
    """A datetime as it is; a date as the start of that day, or the next, in Eastern time.

    ``next_day`` is for ``dateCreated<``: without it the named day itself would silently
    drop out of the range, which reads as missing data rather than an off-by-one.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    day = value + timedelta(days=1) if next_day else value
    return datetime(day.year, day.month, day.day, tzinfo=PLATFORM_TZ)
