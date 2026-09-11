"""Which shapes of alpha this market has already been asked for.

Splitting the labs by search axis keeps them looking for different *things*. It does not,
on its own, stop them building the same *shape*: Sweep trying a proven pattern over a new
field and Pair combining two fields both end up emitting
``group_rank(ts_rank(…), subindustry)``, and the platform judges an alpha on what its
returns look like, not on which lab had the idea.

That matters because self-correlation is the binding constraint on a submitted book, and
three independent projects that ran at scale found the same thing about it:

* alphas sharing three expression legs correlated 0.75 to 0.93 whatever field they were
  anchored on;
* after three to five submitted alphas from one operator family, every further variant
  crossed the 0.7 limit, and re-tuning windows or neutralization moved it by under 0.1;
* the escape was always a structurally different expression, never a different field.

So this counts finished work by **skeleton** — the expression with its data fields and
numbers removed — and reports which shapes are worked out. A saturated shape is one the
market has already answered: either it has been tried enough times to have produced
nothing, or it has already produced as many submittable alphas as it is going to before
they start colliding with each other.

Nothing here blocks anything. It removes a shape from the menu a lab draws from and says
so in the preview, which is the difference between a lab that gets narrower over time and
one that keeps spending the allowance relearning an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import select

from ..catalog.queries import Tuple4
from ..db.duck import Catalog
from ..db.models import SimStatus, SimulationRecord, utcnow
from ..db.sqlite import Database
from ..templates.validate import GROUPING_FIELDS, fields_in
from .yields import is_submittable

log = structlog.get_logger(__name__)

#: Tried this many times with nothing to show for it, and the shape is answered. Set
#: above the size of a single lab's batch so one unlucky run cannot retire a pattern.
DEAD_AFTER = 40

#: Submittable alphas of one shape before further ones start correlating with each
#: other rather than adding to the book. The measured wall is three to five; the lower
#: end is used because crossing it wastes a submission slot, and stopping early only
#: costs a redirection.
CROWDED_AT = 3

#: How far back to look. Long enough to accumulate evidence, short enough that a shape
#: retired months ago comes back once the market has moved on.
WINDOW_DAYS = 30

_NUMBER = re.compile(r"\b\d+\.\d+|\b\d+\b")
_SPACE = re.compile(r"\s+")


def skeleton(expression: str | None) -> str:
    """An expression reduced to its shape.

    Data fields become ``F``, whole numbers ``N``, decimals ``D``. Grouping fields are
    left alone: ``group_rank(F, subindustry)`` and ``group_rank(F, sector)`` compare
    different things and are genuinely different shapes, which is why Sweep offers the
    grouping as a lever in the first place.

    >>> skeleton("group_rank(ts_rank(ts_backfill(assets, 120), 252), subindustry)")
    'group_rank(ts_rank(ts_backfill(F,N),N),subindustry)'
    """
    if not expression:
        return ""

    text = expression.strip()
    # Only the last statement of a multi-statement program decides the shape; the
    # assignments above it are working, and naming them differently is not a new idea.
    if ";" in text:
        text = text.rsplit(";", 1)[-1]

    for name in sorted(fields_in(text), key=len, reverse=True):
        if name in GROUPING_FIELDS:
            continue
        text = re.sub(rf"\b{re.escape(name)}\b", "F", text)

    text = _NUMBER.sub(lambda m: "D" if "." in m.group(0) else "N", text)
    return _SPACE.sub("", text)


@dataclass(frozen=True, slots=True)
class ShapeRecord:
    """What one shape has produced in this market."""

    shape: str
    tried: int
    submittable: int

    @property
    def saturated(self) -> bool:
        """Whether the market has already answered this shape."""
        return self.submittable >= CROWDED_AT or (
            self.tried >= DEAD_AFTER and self.submittable == 0
        )

    @property
    def why(self) -> str:
        if self.submittable >= CROWDED_AT:
            return (
                f"{self.submittable} alphas of this shape already passed; more of them "
                "would be too alike to submit"
            )
        return f"tried {self.tried} times here and none passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "tried": self.tried,
            "submittable": self.submittable,
            "saturated": self.saturated,
            "why": self.why,
        }


class SkeletonBook:
    """What this market has already been asked, by shape rather than by field."""

    def __init__(self, db: Database, catalog: Catalog) -> None:
        self.db = db
        self.catalog = catalog

    async def records(self, scope: Tuple4, *, days: int = WINDOW_DAYS) -> dict[str, ShapeRecord]:
        """Every shape tried in this scope recently, keyed by skeleton."""
        since = utcnow() - timedelta(days=days)

        async with self.db.session() as session:
            rows = (
                await session.execute(
                    select(
                        SimulationRecord.expression,
                        SimulationRecord.alpha_id,
                        SimulationRecord.status,
                    ).where(
                        SimulationRecord.created_at >= since,
                        SimulationRecord.region == scope.region,
                        SimulationRecord.delay == scope.delay,
                    )
                )
            ).all()

        finished = [
            (expression, alpha_id)
            for expression, alpha_id, status in rows
            if expression and SimStatus(status).terminal
        ]
        if not finished:
            return {}

        verdicts = await self._submittable({a for _, a in finished if a})

        tally: dict[str, list[int]] = {}
        for expression, alpha_id in finished:
            shape = skeleton(expression)
            if not shape:
                continue
            entry = tally.setdefault(shape, [0, 0])
            entry[0] += 1
            if alpha_id and verdicts.get(str(alpha_id)):
                entry[1] += 1

        return {
            shape: ShapeRecord(shape=shape, tried=tried, submittable=passed)
            for shape, (tried, passed) in tally.items()
        }

    async def saturated(self, scope: Tuple4, *, days: int = WINDOW_DAYS) -> dict[str, ShapeRecord]:
        """Only the shapes that are worked out."""
        return {s: r for s, r in (await self.records(scope, days=days)).items() if r.saturated}

    async def is_saturated(self, scope: Tuple4, expression: str) -> bool:
        """Whether one candidate expression's shape is already answered here."""
        return skeleton(expression) in await self.saturated(scope)

    async def _submittable(self, alpha_ids: set[str]) -> dict[str, bool]:
        """Chunked: DuckDB takes ids as literal parameters and a month is thousands."""
        if not alpha_ids:
            return {}
        ordered = sorted(alpha_ids)
        out: dict[str, bool] = {}
        for start in range(0, len(ordered), 500):
            chunk = ordered[start : start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            rows = await self.catalog.query(
                f"SELECT alpha_id, checks FROM alpha WHERE alpha_id IN ({placeholders})",
                chunk,
            )
            for row in rows:
                out[str(row["alpha_id"])] = is_submittable(row.get("checks"))
        return out


def drop_saturated(
    candidates: list[str], saturated: dict[str, ShapeRecord]
) -> tuple[list[str], list[str]]:
    """Split candidate expressions into the ones worth running and the ones answered.

    Returned rather than filtered in place so the caller can say which shapes it left
    out. A lab that quietly runs less than it was asked for is the failure this whole
    application is built to avoid.
    """
    keep: list[str] = []
    dropped: list[str] = []
    for expression in candidates:
        if skeleton(expression) in saturated:
            dropped.append(expression)
        else:
            keep.append(expression)
    return keep, dropped
