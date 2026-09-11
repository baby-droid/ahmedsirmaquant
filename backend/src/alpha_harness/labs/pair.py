"""Pair: two data fields at once, and what sits between them.

Sweep asks *which* field carries a signal. This asks a question Sweep structurally
cannot: what does one field say **relative to another**. That is a different axis, not a
different algorithm, which is the only reason it is a separate lab.

It is also where most of the empirically strong alphas in the reference material live.
The shapes that keep working are ratios and gaps between two quantities — operating
income against equity, an analyst estimate against price, free cash flow against
equity — because a raw quantity is not comparable across companies and a ratio is. The
platform's own documentation makes the same point in its worked examples, which combine
a model's earnings signal with its price signal and subtract its valuation signal.

**Pairs are drawn across datasets, never within one.** Two fields from the same vendor
feed move together, so pairing them mostly restates one of them. Crossing datasets is
what makes the relationship informative, and it is also what keeps the result far from
the single-field alphas already in the book.

**The draw is seeded per day.** Five hundred consultants funding this lab on the same
morning must not receive the same pairs, and there is no user id anywhere in the seed —
the spread comes from the day, the market, and how deep into each dataset the caller
asked to look.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import structlog

from ..brain.schemas import SimulationRequest, SimulationSettings
from ..catalog.queries import CatalogQueries, Tuple4
from ..harvest.patterns import GROUPS, WINDOWS
from ..harvest.seeds import SeedHarvester
from ..templates.validate import (
    POWER_POOL_MAX_FIELDS,
    POWER_POOL_MAX_OPERATORS,
    count_operators,
    operators_in,
    unique_data_fields,
)

log = structlog.get_logger(__name__)

#: Never more than this in one go, matching the other labs.
MAX_SIMULATIONS = 20_000

#: Below this a field cannot carry a signal about the market whatever else is true.
MIN_COVERAGE = 0.5

#: Price and size anchors. A fundamental quantity measured against price or market value
#: is the oldest working idea in the subject, and these three are present in every
#: equity market the platform offers.
ANCHORS: tuple[str, ...] = ("close", "cap", "adv20")

#: Every field is backfilled first. Most data outside price and volume updates quarterly
#: or on an event, and ``ts_backfill`` costs nothing against the operator budget.
BACKFILL = 120


@dataclass(frozen=True, slots=True)
class Shape:
    """One way of relating two fields."""

    name: str
    template: str
    #: What it is betting on, in the words a beginner needs.
    idea: str
    operators: tuple[str, ...]
    grouped: bool = False

    def render(self, a: str, b: str, window: int, group: str) -> str:
        left = f"ts_backfill({a}, {BACKFILL})"
        right = f"ts_backfill({b}, {BACKFILL})"
        return self.template.format(a=left, b=right, window=window, group=group)


SHAPES: tuple[Shape, ...] = (
    Shape(
        "ratio",
        "group_rank(ts_rank({a} / {b}, {window}), {group})",
        "One number measured against another, then compared with the industry. A big "
        "company's big number stops looking like a strong signal.",
        operators=("group_rank", "ts_rank", "ts_backfill"),
        grouped=True,
    ),
    Shape(
        "gap",
        "rank(ts_zscore({a}, {window})) - rank(ts_zscore({b}, {window}))",
        "Which of the two is unusual right now, relative to the other. Bets on the gap "
        "between them closing.",
        operators=("rank", "ts_zscore", "ts_backfill"),
    ),
    Shape(
        "link",
        "-ts_corr({a}, {b}, {window})",
        "How tightly the two have moved together lately. Bets against the ones that "
        "have started moving in lockstep.",
        operators=("ts_corr", "ts_backfill"),
    ),
    Shape(
        "residual",
        "group_neutralize(ts_regression({a}, {b}, {window}, lag = 0, rettype = 0), {group})",
        "The part of one number the other does not explain. What is left is specific to "
        "the company rather than to whatever they have in common.",
        operators=("group_neutralize", "ts_regression", "ts_backfill"),
        grouped=True,
    ),
    Shape(
        "product",
        "group_neutralize(ts_rank({a} * {b}, {window}), {group})",
        "Both together, so it only fires when the two agree. Fewer positions, stronger "
        "conviction in each.",
        operators=("group_neutralize", "ts_rank", "ts_backfill"),
        grouped=True,
    ),
)

SHAPES_BY_NAME = {s.name: s for s in SHAPES}


def catalogue() -> list[dict[str, Any]]:
    return [
        {
            "name": s.name,
            "idea": s.idea,
            "grouped": s.grouped,
            "example": s.render("operating_income", "equity", 252, "subindustry"),
        }
        for s in SHAPES
    ]


class Pairer:
    """Builds alphas out of two fields from different datasets."""

    def __init__(
        self,
        queries: CatalogQueries,
        harvester: SeedHarvester,
        skeletons: Any = None,
        auth: Any = None,
    ) -> None:
        self.queries = queries
        self.harvester = harvester
        self.skeletons = skeletons
        #: Optional. Used only to read the cached operator list, so a shape the account
        #: cannot run is dropped rather than discovered through a failed simulation.
        self.auth = auth

    async def plan(
        self,
        *,
        scope: Tuple4,
        target: int = 500,
        shapes: list[str] | None = None,
        windows: list[int] | None = None,
        group: str = "subindustry",
        neutralization: str = "SUBINDUSTRY",
        decay: int = 4,
        truncation: float = 0.08,
        per_dataset: int = 1,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Pairs to try, and the simulations that would run them."""
        target = max(1, min(target, MAX_SIMULATIONS))
        if group not in GROUPS:
            raise ValueError(f"{group!r} is not a grouping field. Use one of: {', '.join(GROUPS)}.")

        chosen = [SHAPES_BY_NAME[s] for s in (shapes or [])] or list(SHAPES)
        chosen = await self._runnable(chosen)
        chosen, worked_out = await self._drop_worked_out(scope, chosen, group)
        chosen_windows = windows or list(WINDOWS)

        fields = await self._fields(scope, per_dataset)
        if len(fields) < 2:
            return _nothing(
                "There is not enough downloaded data in this market to pair anything up "
                "yet. Download it, or run a day of exploring first."
            )

        pairs = self._draw(fields, target, len(chosen) * len(chosen_windows), scope, seed)
        if not pairs:
            return _nothing(
                "Every field in this market comes from the same dataset, so there is "
                "nothing to pair it with that would say anything new."
            )

        settings = SimulationSettings(
            instrumentType=scope.instrument_type,
            region=scope.region,
            delay=scope.delay,
            universe=scope.universe,
            neutralization=neutralization,
            decay=decay,
            truncation=truncation,
        )

        requests: list[SimulationRequest] = []
        described: list[dict[str, Any]] = []
        # Pair varies fastest, so truncating at the target leaves a broad spread of
        # pairs rather than every shape of the first two fields.
        for shape in chosen:
            for window in chosen_windows:
                for a, b in pairs:
                    if len(requests) >= target:
                        break
                    expression = shape.render(a["field_id"], b["field_id"], window, group)
                    if not _within_power_pool(expression):
                        continue
                    requests.append(
                        SimulationRequest(type="REGULAR", settings=settings, regular=expression)
                    )
                    if len(described) < 12:
                        described.append(
                            {
                                "shape": shape.name,
                                "idea": shape.idea,
                                "a": a["field_id"],
                                "b": b["field_id"],
                                "fromDatasets": [a["dataset_id"], b["dataset_id"]],
                                "window": window,
                                "expression": expression,
                            }
                        )

        log.info(
            "pair.planned",
            scope=scope.label,
            pairs=len(pairs),
            shapes=[s.name for s in chosen],
            simulations=len(requests),
        )
        return {
            "requests": requests,
            "pairs": described,
            "possible": len(pairs) * len(chosen) * len(chosen_windows),
            "fields": len(fields),
            "datasets": len({f["dataset_id"] for f in fields}),
            "shapes": [s.name for s in chosen],
            "windows": chosen_windows,
            "workedOut": worked_out,
            "settings": settings.model_dump(by_alias=True, exclude_none=True),
            "note": (
                f"Pairs are drawn across {len({f['dataset_id'] for f in fields})} different "
                "datasets. Two fields from the same feed move together, so pairing them "
                "would mostly restate one of them."
            ),
        }

    # -- choosing what to pair -------------------------------------------

    async def _fields(self, scope: Tuple4, per_dataset: int) -> list[dict[str, Any]]:
        """The best few fields per dataset, plus the price and size anchors."""
        fields = await self.harvester.best_fields(
            scope, per_dataset=per_dataset, min_coverage=MIN_COVERAGE, limit=60
        )
        known = {str(f["field_id"]) for f in fields}

        placeholders = ", ".join("?" for _ in ANCHORS)
        anchors = await self.queries.catalog.query(
            f"""
            SELECT field_id, dataset_id, description, coverage
            FROM data_field
            WHERE {Tuple4.WHERE} AND field_id IN ({placeholders})
            """,
            [*scope.params, *ANCHORS],
        )
        combined = [*fields, *(a for a in anchors if str(a["field_id"]) not in known)]

        # Sorted before anything is drawn from it. Coverage alone leaves ties — a
        # hundred fields can share it — and the database is free to return tied rows in
        # any order, which would make the same seed produce a different draw each time.
        # A seed nobody can reproduce is not a seed.
        combined.sort(key=lambda f: (-(f.get("coverage") or 0.0), str(f["field_id"])))
        return combined

    def _draw(
        self,
        fields: list[dict[str, Any]],
        target: int,
        per_pair: int,
        scope: Tuple4,
        seed: int | None,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Cross-dataset pairs, in an order this consultant is unlikely to share.

        Seeded from the day and the market rather than from anything identifying, so the
        same person gets a stable answer while they are looking at it and a different one
        tomorrow. Two people on the same day get the same *order*, but their field lists
        differ — the catalog they synced, the depth they asked for and what they already
        own all feed the list this shuffles.
        """
        from ..plan.day import today

        rng = random.Random(seed if seed is not None else f"{today()}-{scope.label}")

        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for i, a in enumerate(fields):
            for b in fields[i + 1 :]:
                if a["dataset_id"] and a["dataset_id"] == b["dataset_id"]:
                    continue
                candidates.append((a, b))

        rng.shuffle(candidates)
        needed = max(1, -(-target // max(1, per_pair)))
        return candidates[:needed]

    async def _runnable(self, chosen: list[Shape]) -> list[Shape]:
        """Drop shapes whose operators this account does not have.

        An unavailable operator fails the simulation and still spends the allowance, and
        the operator list is already cached from sign-in.
        """
        if self.auth is None:
            return chosen
        try:
            cached = await self.auth.cached_operators()
        except Exception:
            return chosen
        if not cached:
            return chosen

        known = {str(o.get("name")) for o in cached}
        keep = [s for s in chosen if set(s.operators) <= known]
        if not keep:
            # A permission is a reason to search differently, never a reason to stop.
            log.info("pair.no_shape_available", operators=len(known))
            return chosen
        if len(keep) != len(chosen):
            log.info(
                "pair.shapes_unavailable",
                dropped=[s.name for s in chosen if s not in keep],
            )
        return keep

    async def _drop_worked_out(
        self, scope: Tuple4, chosen: list[Shape], group: str
    ) -> tuple[list[Shape], list[str]]:
        """Leave out shapes this market has already answered."""
        if self.skeletons is None or not chosen:
            return chosen, []
        try:
            saturated = await self.skeletons.saturated(scope)
        except Exception:
            log.warning("pair.skeletons_unavailable", scope=scope.label, exc_info=True)
            return chosen, []
        if not saturated:
            return chosen, []

        from ..plan.skeletons import skeleton

        keep = [s for s in chosen if skeleton(s.render("a", "b", 252, group)) not in saturated]
        if not keep:
            return chosen, []
        return keep, [s.name for s in chosen if s not in keep]


def _within_power_pool(expression: str) -> bool:
    """Power Pool limits: at most eight operators and three non-grouping fields.

    Checked rather than assumed. A shape is written to fit, but a field name that
    happens to look like an operator call, or a template edited later, should not
    silently produce an alpha that can never be eligible.
    """
    return (
        count_operators(expression) <= POWER_POOL_MAX_OPERATORS
        and len(unique_data_fields(expression)) <= POWER_POOL_MAX_FIELDS
        and bool(operators_in(expression))
    )


def _nothing(note: str) -> dict[str, Any]:
    return {
        "requests": [],
        "pairs": [],
        "possible": 0,
        "fields": 0,
        "datasets": 0,
        "shapes": [],
        "windows": [],
        "workedOut": [],
        "settings": {},
        "note": note,
    }
