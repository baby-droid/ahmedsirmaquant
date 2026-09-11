"""Deepen: the numbers inside an idea that already works.

Every other lab looks for a new idea. This one takes an idea that already scored and asks
what its numbers should have been — the lookbacks, the decay, the truncation, what each
stock is compared against. It is the narrowest search in the product and the only one
that goes *deeper* rather than wider.

**It does not answer with a list of simulations.** Tuning is a loop: ask for a round, wait
for it to finish, learn from it, ask for the next one. So this module does not build
requests — it builds a *template*, and the optimiser drives the rounds. That is why the
dispatcher starts this desk rather than queueing it.

**Which numbers get opened up.** Whole numbers in the expression that look like lookbacks:
five and above, and not the ``ts_backfill`` window, which describes how missing data is
filled rather than what the alpha measures — searching it would spend a whole dimension
on a decision nobody meant to make. Each opened-up number keeps its original value in its
grid alongside the conventional windows either side of it, so the study can always
reproduce the alpha it started from and every trial is measured against it.

**And the settings, always.** Even an expression with no numbers in it at all is worth
tuning: decay, truncation and neutralization are as much a part of an alpha as its
formula, and they are the cheapest thing in the product to get wrong.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from ..catalog.queries import Tuple4
from .harden import MIN_WINDOW, neighbours

log = structlog.get_logger(__name__)

#: How many alphas one day's tuning works on. Each becomes a study of its own, and a
#: study that never gets past its first round has learned nothing.
SEEDS = 4

#: A round fills whole batches: a multi-simulation carries ten children.
BATCH = 10

#: At most this many numbers are searched at once. A study over six dimensions with a
#: few hundred trials is a random search wearing an optimiser's hat.
MAX_WINDOWS = 3

#: Only alphas at or above this are worth deepening. Tuning a signal that is not there
#: finds the best version of nothing.
MIN_SHARPE = 1.0

DECAY_GRID: list[int] = [0, 4, 10, 20]
TRUNCATION_GRID: list[float] = [0.05, 0.08]
NEUTRALIZATIONS: list[str] = ["SUBINDUSTRY", "INDUSTRY", "MARKET"]

#: A whole number that is not part of a name and not a decimal.
_INTEGER = re.compile(r"(?<![\w.])(\d+)(?![\w.])")

#: The second argument of a ``ts_backfill`` call — how far back to look for a value to
#: carry forward. Not a lookback in the sense that matters here.
_BACKFILL = re.compile(r"ts_backfill\s*\([^()]*,\s*(\d+)\s*\)")


async def seeds(
    vault: Any,
    scope: Tuple4,
    *,
    min_sharpe: float = MIN_SHARPE,
    limit: int = SEEDS,
) -> list[dict[str, Any]]:
    """The alphas in this market worth tuning, best first."""
    rows = await vault.alphas(
        region=scope.region,
        delay=scope.delay,
        universe=scope.universe,
        instrument_type=scope.instrument_type,
        min_sharpe=min_sharpe,
        limit=limit * 5,
    )

    chosen: list[dict[str, Any]] = []
    for row in rows:
        status = row.get("status")
        if status is not None and str(status).upper() != "UNSUBMITTED":
            continue
        if not str(row.get("expression") or "").strip():
            continue
        chosen.append(row)
        if len(chosen) >= limit:
            break
    return chosen


def template_for(row: dict[str, Any], *, name: str) -> str:
    """One seed alpha turned into a template the optimiser can search.

    Returned as YAML because that is what a study stores and what the Template Studio
    shows: someone who wants to see what is being tuned reads the same document the
    machine does.
    """
    import yaml

    expression = str(row.get("expression") or "").strip()
    if not expression:
        raise ValueError("This alpha has no expression to tune.")

    templated, variables = open_up(expression)
    data: dict[str, Any] = {
        "name": name,
        "description": (
            f"Tuning {row.get('alpha_id')}, which scored "
            f"{float(row.get('sharpe') or 0.0):.2f}. The formula is held still and only "
            "its numbers and settings are searched."
        ),
        "expr": templated,
        "vars": variables,
        "settings": {
            "instrumentType": str(row.get("instrument_type") or "EQUITY"),
            "region": str(row.get("region") or ""),
            "delay": int(row.get("delay") or 1),
            "universe": str(row.get("universe") or ""),
            "neutralization": _neutralizations(row),
            "decay": DECAY_GRID,
            "truncation": TRUNCATION_GRID,
        },
        "constraints": {"power_pool": True},
    }
    return str(yaml.safe_dump(data, sort_keys=False, default_flow_style=False, width=88))


def open_up(expression: str) -> tuple[str, dict[str, Any]]:
    """Replace the lookbacks with variables, and describe each one.

    >>> open_up("group_rank(ts_rank(ts_backfill(assets, 120), 252), subindustry)")[0]
    'group_rank(ts_rank(ts_backfill(assets, 120), $w0), subindustry)'
    """
    # Worked out before anything is replaced: the spans shift as soon as the text does.
    protected = {match.span(1) for match in _BACKFILL.finditer(expression)}

    variables: dict[str, Any] = {}
    pieces: list[str] = []
    cursor = 0

    for match in _INTEGER.finditer(expression):
        if len(variables) >= MAX_WINDOWS:
            break
        if match.span(1) in protected:
            continue
        value = int(match.group(1))
        if value < MIN_WINDOW:
            continue

        key = f"w{len(variables)}"
        variables[key] = {
            "type": "int",
            "description": f"Was {value} in the alpha this came from.",
            # The original value is always in the grid, so every trial is measured
            # against the alpha that earned this study in the first place.
            "grid": sorted({value, *neighbours(value)}),
        }
        pieces.append(expression[cursor : match.start(1)])
        pieces.append(f"${key}")
        cursor = match.end(1)

    pieces.append(expression[cursor:])
    return "".join(pieces), variables


def _neutralizations(row: dict[str, Any]) -> list[str]:
    """What each stock is compared against, the alpha's own choice first."""
    current = str(row.get("neutralization") or "").upper()
    ordered = [current] if current else []
    ordered.extend(n for n in NEUTRALIZATIONS if n != current)
    return ordered
