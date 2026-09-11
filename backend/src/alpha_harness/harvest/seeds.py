"""From a synced scope to a day's worth of simulations.

The goal is blunt: someone who has never written an alpha should be able to pick a
region and a size, and have a thousand sensible simulations queued. Everything here
serves that.

**Which fields.** Not all of them — a scope holds tens of thousands and most are thin or
already crowded. One field per dataset, which spreads the search across the data rather
than burying it in whichever dataset happens to be largest, and only fields that already
carry at least a few alphas, because a field nobody has ever built on usually turns out
to have a reason. Within a dataset the pick is the widest coverage: a field present on
4% of the universe cannot produce a signal about the market however good the idea is.

**Which shapes.** The plain ones in :mod:`.patterns`. At this stage volume beats
cleverness — a hundred ordinary alphas across a hundred fields finds more than one
elaborate alpha on one field.

**How it packs.** Everything generated shares one region, delay, instrument type and
language, which are four of the five fields a multi-simulation's children must agree on.
So a harvest of a thousand fills whole batches from the first slot to the last, rather
than fragmenting into a thousand singletons.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

import structlog

from ..brain.schemas import SimulationRequest, SimulationSettings
from ..catalog.queries import CatalogQueries, Tuple4
from .patterns import GROUPS, PATTERNS, PATTERNS_BY_NAME, WINDOWS, Pattern

log = structlog.get_logger(__name__)

#: A field with fewer alphas than this is usually unusable rather than undiscovered.
DEFAULT_MIN_ALPHAS = 3
#: Below this, the signal covers too little of the market to matter.
DEFAULT_MIN_COVERAGE = 0.6
#: Ceiling on one harvest. A day's quota is the real limit; this stops a slip of the
#: hand queueing a hundred thousand.
MAX_SIMULATIONS = 20_000


@dataclass(slots=True)
class Harvest:
    """What a recipe produced, and what it will cost."""

    requests: list[SimulationRequest] = dc_field(default_factory=list)
    fields: list[dict[str, Any]] = dc_field(default_factory=list)
    patterns: list[str] = dc_field(default_factory=list)
    windows: list[int] = dc_field(default_factory=list)
    settings: dict[str, Any] = dc_field(default_factory=dict)
    truncated: bool = False
    possible: int = 0
    #: Shapes left out because this market has already answered them. Reported rather
    #: than silently skipped — a lab that runs less than it was asked for has to say so.
    worked_out: list[str] = dc_field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.requests)

    def summary(self, *, sample: int = 12) -> dict[str, Any]:
        batches = -(-self.count // 10)
        return {
            "simulations": self.count,
            "possible": self.possible,
            "truncated": self.truncated,
            "fields": len(self.fields),
            "patterns": self.patterns,
            "windows": self.windows,
            "settings": self.settings,
            # One batch key by construction, so packing is always perfect.
            "batches": batches,
            "fullBatches": self.count // 10,
            "efficiency": round(self.count / (batches * 10), 3) if batches else 0.0,
            "workedOut": self.worked_out,
            "sample": [
                {"expression": r.regular, "settings": r.settings.model_dump(by_alias=True)}
                for r in self.requests[:sample]
            ],
            "fieldSample": self.fields[:sample],
        }


class SeedHarvester:
    """Turns a scope into simulations without the user writing anything."""

    def __init__(self, queries: CatalogQueries, skeletons: Any = None) -> None:
        self.queries = queries
        #: Optional. When present, shapes this market has already answered are dropped
        #: from the pattern list — see :mod:`..plan.skeletons` for why that matters more
        #: than choosing a different field.
        self.skeletons = skeletons

    async def _drop_worked_out(
        self, scope: Tuple4, chosen: list[Pattern], group: str
    ) -> tuple[list[Pattern], list[str]]:
        """Leave out shapes this market has already answered.

        Which field a shape is anchored on barely moves its correlation with the alphas
        it has already produced, so re-running a worked-out shape over fresh data mostly
        buys rejections. Dropping it here is what makes a second day of Sweep different
        research rather than the same research on different fields.

        If *every* shape is worked out the list is left alone, for the same reason the
        crowding and depth levers give way below: a filter narrows what a lab finds and
        must never be the reason it finds nothing.
        """
        if self.skeletons is None or not chosen:
            return chosen, []

        try:
            saturated = await self.skeletons.saturated(scope)
        except Exception:
            # Never let a bookkeeping query cost the day's work.
            log.warning("harvest.skeletons_unavailable", scope=scope.label, exc_info=True)
            return chosen, []

        if not saturated:
            return chosen, []

        from ..plan.skeletons import skeleton

        keep = [p for p in chosen if skeleton(p.render("field", 252, group)) not in saturated]
        if not keep:
            log.info("harvest.every_shape_worked_out", scope=scope.label)
            return chosen, []

        dropped = [p.name for p in chosen if p not in keep]
        if dropped:
            log.info("harvest.shapes_worked_out", scope=scope.label, dropped=dropped)
        return keep, dropped

    async def best_fields(
        self,
        scope: Tuple4,
        *,
        per_dataset: int = 1,
        min_alphas: int = DEFAULT_MIN_ALPHAS,
        max_alphas: int | None = None,
        min_coverage: float = DEFAULT_MIN_COVERAGE,
        limit: int = 200,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        """The best few fields in each dataset.

        Per dataset rather than overall, so the search spreads across the data instead
        of collapsing into whichever dataset is biggest. ``max_alphas`` is the knob that
        turns this from "the obvious fields" into "the ones nobody has worked yet".

        ``skip`` takes the *next* best fields instead of the best, and exists for one
        reason: without it every consultant who picks the same options gets byte-for-byte
        the same field list, and five hundred people spend their allowance discovering
        the same alphas. Moving one rank down the list inside every dataset changes the
        entire run while costing nothing in quality — the second-widest field in a
        dataset is not meaningfully worse than the widest.
        """
        clauses = [
            "instrument_type = ?",
            "region = ?",
            "delay = ?",
            "universe = ?",
            "field_type = 'MATRIX'",
            "coverage >= ?",
            "alpha_count >= ?",
        ]
        params: list[Any] = [*scope.params, min_coverage, min_alphas]
        if max_alphas is not None:
            clauses.append("alpha_count <= ?")
            params.append(max_alphas)

        return await self.queries.catalog.query(
            f"""
            SELECT field_id, dataset_id, category_id, subcategory_id, description,
                   coverage, alpha_count, user_count, pyramid_multiplier
            FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY dataset_id
                    -- Widest coverage first, and the least worked of equals. A field
                    -- present on most of the universe with few alphas on it is the
                    -- best of both.
                    ORDER BY coverage DESC NULLS LAST, alpha_count ASC
                ) AS rank_in_dataset
                FROM data_field
                WHERE {" AND ".join(clauses)}
            )
            WHERE rank_in_dataset > ? AND rank_in_dataset <= ?
            ORDER BY coverage DESC NULLS LAST, alpha_count ASC
            LIMIT ?
            """,
            [*params, skip, skip + per_dataset, limit],
        )

    async def harvest(
        self,
        scope: Tuple4,
        *,
        target: int = 1000,
        patterns: list[str] | None = None,
        windows: list[int] | None = None,
        group: str = "industry",
        neutralization: str = "SUBINDUSTRY",
        decay: int = 0,
        truncation: float = 0.08,
        per_dataset: int = 1,
        min_alphas: int = DEFAULT_MIN_ALPHAS,
        max_alphas: int | None = None,
        min_coverage: float = DEFAULT_MIN_COVERAGE,
        fields: list[str] | None = None,
        skip: int = 0,
    ) -> Harvest:
        """Generate up to ``target`` simulations for one scope.

        ``fields`` overrides the automatic pick. That is how a conversation becomes a
        run: the assistant reads a hunch, names the fields that could measure it, and
        those exact fields come back here. It is also where the diversity comes from —
        five hundred consultants describe five hundred different hunches, and no menu
        arranges that.
        """
        target = max(1, min(target, MAX_SIMULATIONS))
        chosen = [PATTERNS_BY_NAME[p] for p in (patterns or [])] or list(PATTERNS)
        chosen_windows = windows or list(WINDOWS)
        if group not in GROUPS:
            raise ValueError(f"{group!r} is not a grouping field. Use one of: {', '.join(GROUPS)}.")

        chosen, worked_out = await self._drop_worked_out(scope, chosen, group)
        per_field = len(chosen) * len(chosen_windows)

        if fields:
            # Chosen for us — by the assistant, from a hunch someone typed. Looked up so
            # the preview shows real coverage and crowding rather than bare names, and
            # anything not present in this scope is dropped: simulating a field that
            # does not exist here spends allowance to learn nothing.
            fields_used = await self._lookup(scope, fields)
        else:
            # Enough fields to reach the target, without asking the catalog for more
            # than the recipe can use.
            needed = max(1, -(-target // max(1, per_field)))

            # One field per dataset caps the pool at the number of datasets, which is a
            # few hundred at most. A target of 1,667 then quietly delivers 480 — the
            # exact failure this application exists to prevent, wearing a different hat.
            # So reach deeper into each dataset when the target demands it, keeping the
            # caller's value as a floor.
            datasets = await self._dataset_count(scope, min_alphas, max_alphas, min_coverage)
            if datasets:
                per_dataset = max(per_dataset, -(-needed // datasets))

            pick: dict[str, Any] = {
                "per_dataset": per_dataset,
                "min_alphas": min_alphas,
                "max_alphas": max_alphas,
                "min_coverage": min_coverage,
                "limit": needed,
            }
            # Depth counts in blocks, not in single ranks: with fourteen fields taken
            # from each dataset, stepping down by one would overlap the previous track
            # thirteen ways out of fourteen and the tracks would run the same data.
            fields_used = await self.best_fields(scope, **pick, skip=skip * per_dataset)
            if not fields_used and skip:
                # Ranking happens *after* the crowding filter, so a narrow filter can
                # leave a single field in each dataset and nothing at all below it.
                # "Data nobody has used" and "look one field deeper" would then be
                # mutually exclusive, and the combination silently produced no work at
                # all. Depth is a way of finding different fields, never a reason to
                # find none, so it gives way to the filter that was actually asked for.
                log.info("harvest.depth_exhausted", scope=scope.label, skip=skip)
                fields_used = await self.best_fields(scope, **pick, skip=0)

            if not fields_used and (max_alphas is not None or min_alphas > DEFAULT_MIN_ALPHAS):
                # The crowding filter matched nothing in this market. "Data that has
                # already produced alphas" is a reasonable thing to ask for and a thin
                # market may simply not contain any, and the same rule applies as to
                # depth: a lever narrows *which* fields are found, and must never be the
                # reason none are. Without this the primary desk can be funded, draw a
                # filter this market cannot satisfy, and queue nothing at all.
                log.info(
                    "harvest.crowding_relaxed",
                    scope=scope.label,
                    min_alphas=min_alphas,
                    max_alphas=max_alphas,
                )
                fields_used = await self.best_fields(
                    scope,
                    per_dataset=per_dataset,
                    min_alphas=DEFAULT_MIN_ALPHAS,
                    max_alphas=None,
                    min_coverage=min_coverage,
                    limit=needed,
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

        result = Harvest(
            fields=fields_used,
            patterns=[p.name for p in chosen],
            windows=chosen_windows,
            settings=settings.model_dump(by_alias=True, exclude_none=True),
            possible=len(fields_used) * per_field,
            worked_out=worked_out,
        )

        # Field varies fastest so that if the target truncates the list, what survives
        # is a broad sweep across many fields rather than every window of the first few.
        for pattern in chosen:
            for window in chosen_windows:
                for row in fields_used:
                    if len(result.requests) >= target:
                        result.truncated = result.possible > target
                        log.info(
                            "harvest.generated",
                            scope=scope.label,
                            simulations=len(result.requests),
                            fields=len(fields_used),
                        )
                        return result
                    result.requests.append(
                        _request(pattern, str(row["field_id"]), window, group, settings)
                    )

        log.info(
            "harvest.generated",
            scope=scope.label,
            simulations=len(result.requests),
            fields=len(fields_used),
        )
        return result

    async def _dataset_count(
        self,
        scope: Tuple4,
        min_alphas: int,
        max_alphas: int | None,
        min_coverage: float,
    ) -> int:
        """How many datasets hold a usable field here — the width of the search."""
        clauses = [
            Tuple4.WHERE,
            "field_type = 'MATRIX'",
            "coverage >= ?",
            "alpha_count >= ?",
        ]
        params: list[Any] = [*scope.params, min_coverage, min_alphas]
        if max_alphas is not None:
            clauses.append("alpha_count <= ?")
            params.append(max_alphas)

        rows = await self.queries.catalog.query(
            f"SELECT count(DISTINCT dataset_id) AS n FROM data_field WHERE {' AND '.join(clauses)}",
            params,
        )
        return int(rows[0]["n"]) if rows else 0

    async def _lookup(self, scope: Tuple4, field_ids: list[str]) -> list[dict[str, Any]]:
        """Look up named fields in one scope, keeping the order asked for."""
        if not field_ids:
            return []
        placeholders = ", ".join("?" for _ in field_ids)
        rows = await self.queries.catalog.query(
            f"""
            SELECT field_id, dataset_id, category_id, subcategory_id, description,
                   coverage, alpha_count, user_count, pyramid_multiplier
            FROM data_field
            WHERE instrument_type = ? AND region = ? AND delay = ? AND universe = ?
              AND field_id IN ({placeholders})
            """,
            [*scope.params, *field_ids],
        )
        by_id = {str(r["field_id"]): r for r in rows}
        return [by_id[f] for f in field_ids if f in by_id]


def _request(
    pattern: Pattern,
    field_id: str,
    window: int,
    group: str,
    settings: SimulationSettings,
) -> SimulationRequest:
    return SimulationRequest(
        type="REGULAR",
        settings=settings,
        regular=pattern.render(field_id, window, group),
    )
