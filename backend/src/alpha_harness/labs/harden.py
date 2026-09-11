"""Harden: find out whether a good result is real.

Optimisation is an inverted U. In-sample Sharpe rises monotonically as you tune;
out-of-sample Sharpe rises, peaks, and then collapses as the parameters start memorising
noise. So a product whose only move is *climb* will eventually hand someone a beautifully
scored alpha that earns nothing. This lab is the counterweight, and it is why Deepen and
Harden are separate directions rather than one: Deepen climbs, Harden checks the ground.

Two tests, both from the research notes.

**Twisting the variables.** A robust alpha lives in a stable neighbourhood, not on a
spike. If it peaks at ``d=45`` with Sharpe 1.8, then ``d=44`` should score about 1.7. If
it collapses to 0.5, the 45 was an artefact of this particular history and nothing more.

**Changing the distribution.** Wrapping the alpha in ``rank`` or ``winsorize`` should not
destroy it. If removing the outliers removes the profit, the profit was the outliers —
a handful of days that will not repeat.

**What makes this worth a simulation.** It costs a few runs to learn that a result is
hollow, against a submission that fails and a self-correlation slot wasted on it. It also
raises Yield Rate directly, which is the number this product is judged on: yield counts
alphas that pass every check, and an overfit alpha fails them later rather than sooner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

from ..brain.schemas import SimulationRequest, SimulationSettings
from ..catalog.queries import CatalogQueries, Tuple4
from ..schemas import camel_dict

log = structlog.get_logger(__name__)

#: Conventional trading windows. A parameter is only worth twisting if it looks like a
#: lookback, and neighbours are drawn from real market cycles rather than from ``d±1``
#: for its own sake — a parameter that only works at 44 and not 42 is the finding.
WINDOWS: tuple[int, ...] = (5, 10, 21, 42, 63, 126, 252, 504)

#: Integers below this are usually not lookbacks — they are ``std=4``, a power, an index.
MIN_WINDOW = 5

#: Transforms that must not destroy a real signal. ``winsorize`` is the sharpest test:
#: it removes the outliers, and an alpha that lived on them dies here.
TRANSFORMS: tuple[tuple[str, str, str], ...] = (
    ("rank", "rank({alpha})", "Ranked instead of raw. Scale should not matter."),
    (
        "winsorize",
        "winsorize({alpha}, std=4)",
        "Outliers trimmed. If the profit was a handful of extreme days, it goes here.",
    ),
    ("zscore", "zscore({alpha})", "Standardised. Tests that the spread is not doing the work."),
)

#: A whole number that is not part of a name and not a decimal.
_INTEGER = re.compile(r"(?<![\w.])(\d+)(?![\w.])")

MAX_SIMULATIONS = 2_000


@dataclass(slots=True)
class Probe:
    """One perturbation of an alpha, and what it would prove."""

    kind: str
    expression: str
    changed: str
    proves: str

    def to_dict(self) -> dict[str, Any]:
        return camel_dict(self)


def neighbours(window: int) -> list[int]:
    """The conventional windows either side of this one."""
    ladder = sorted(WINDOWS)
    if window in ladder:
        index = ladder.index(window)
        return [ladder[i] for i in (index - 1, index + 1) if 0 <= i < len(ladder)]
    # Not a conventional window at all — which is itself a red flag the notes call out,
    # so the nearest two conventional ones are exactly what to compare against.
    return sorted(ladder, key=lambda w: abs(w - window))[:2]


def probes(expression: str) -> list[Probe]:
    """Every way of asking this alpha whether it means it."""
    out: list[Probe] = []

    for match in _INTEGER.finditer(expression):
        value = int(match.group(1))
        if value < MIN_WINDOW:
            continue
        for neighbour in neighbours(value):
            if neighbour == value:
                continue
            twisted = expression[: match.start()] + str(neighbour) + expression[match.end() :]
            out.append(
                Probe(
                    kind="twist",
                    expression=twisted,
                    changed=f"{value} → {neighbour}",
                    proves=(
                        f"Whether {value} was the idea or just the number that happened to fit."
                    ),
                )
            )

    for name, template, proves in TRANSFORMS:
        out.append(
            Probe(
                kind=name,
                expression=template.format(alpha=expression),
                changed=f"wrapped in {name}",
                proves=proves,
            )
        )
    return out


class Hardener:
    """Asks the alphas that scored well whether they meant it."""

    def __init__(self, queries: CatalogQueries) -> None:
        self.queries = queries

    async def plan(
        self,
        *,
        scope: Tuple4,
        min_sharpe: float = 1.25,
        limit: int = 10,
        target: int = 300,
    ) -> dict[str, Any]:
        """What to run to find out which of these results are real. Costs nothing."""
        candidates = await self.queries.catalog.query(
            """
            SELECT alpha_id, expression, sharpe, neutralization, decay, truncation
            FROM alpha
            WHERE instrument_type = ? AND region = ? AND delay = ? AND universe = ?
              AND expression IS NOT NULL AND sharpe IS NOT NULL AND abs(sharpe) >= ?
            ORDER BY abs(sharpe) DESC
            LIMIT ?
            """,
            [*scope.params, min_sharpe, limit],
        )
        if not candidates:
            return {
                "checks": [],
                "requests": [],
                "note": (
                    "Nothing here has scored well enough to be worth checking yet. This "
                    "lab is for after something works."
                ),
            }

        checks: list[dict[str, Any]] = []
        requests: list[SimulationRequest] = []
        for alpha in candidates:
            expression = str(alpha["expression"] or "")
            found = probes(expression)
            if not found:
                continue
            checks.append(
                {
                    "alphaId": str(alpha["alpha_id"]),
                    "expression": expression,
                    "sharpe": alpha["sharpe"],
                    "probes": [p.to_dict() for p in found],
                }
            )
            for probe in found:
                if len(requests) >= min(target, MAX_SIMULATIONS):
                    break
                requests.append(_request(probe.expression, scope, alpha))

        log.info(
            "harden.planned",
            scope=scope.label,
            alphas=len(checks),
            probes=sum(len(c["probes"]) for c in checks),
        )
        return {
            "checks": checks,
            "requests": requests,
            "alphas": len(checks),
            "note": (
                "Each of these runs the same idea with one thing changed. A real idea "
                "barely notices. A lucky one falls apart, and it is much cheaper to find "
                "that out here than after submitting it."
            ),
        }


def _request(expression: str, scope: Tuple4, alpha: dict[str, Any]) -> SimulationRequest:
    """The probe, run under the original alpha's own settings.

    Settings are carried over deliberately: changing them as well would confound the
    test. The point is to vary exactly one thing.
    """
    return SimulationRequest(
        type="REGULAR",
        settings=SimulationSettings(
            instrumentType=scope.instrument_type,
            region=scope.region,
            delay=scope.delay,
            universe=scope.universe,
            neutralization=str(alpha.get("neutralization") or "SUBINDUSTRY"),
            decay=int(alpha.get("decay") or 0),
            truncation=float(alpha.get("truncation") or 0.08),
        ),
        regular=expression,
    )
