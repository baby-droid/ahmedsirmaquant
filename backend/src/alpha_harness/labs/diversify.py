"""Diversify: look deliberately where your own alphas are not.

An alpha whose daily returns correlate above 0.7 with something you have already
submitted cannot be submitted, however good it is. So a consultant who keeps working the
data they know eventually produces excellent results that are all rejected — and the
rejection arrives *after* the simulation is spent. Meanwhile a hundred weakly correlated
mediocre alphas beat three brilliant ones out of sample, because merged volatility falls
with the law of large numbers and the portfolio survives any one of them decaying.

This lab therefore optimises **distance, not score**. That is what separates it from
Sweep, which walks the same axis — which data field — but towards quality. The two are
triggered by opposite evidence: an empty pool calls for Sweep, a crowded one calls for
this.

**How distance is measured.** By dataset. Two fields in the same dataset are built from
the same vendor feed and tend to move together, so a dataset the consultant has never
touched is genuinely new ground in a way that a neighbouring field in a worked dataset is
not. It is a coarse measure and deliberately so: it needs no PnL, no correlation matrix
and no simulations to compute, which means it works on day one when there is nothing to
correlate against yet.

Expression building is left to :class:`SeedHarvester`. This lab chooses *where to look*;
the shapes tried once you are there are not its business, and duplicating them would make
two labs drift apart.
"""

from __future__ import annotations

from typing import Any

import structlog

from ..catalog.queries import CatalogQueries, Tuple4
from ..harvest.seeds import SeedHarvester
from ..templates.validate import unique_data_fields

log = structlog.get_logger(__name__)

#: Below this a field cannot carry a signal about the market whatever else is true of it.
MIN_COVERAGE = 0.5


class Diversifier:
    """Finds the parts of the data this consultant has not been using."""

    def __init__(self, queries: CatalogQueries, harvester: SeedHarvester) -> None:
        self.queries = queries
        self.harvester = harvester

    async def worked_datasets(self, scope: Tuple4) -> set[str]:
        """Which datasets this consultant's own alphas already draw on."""
        rows = await self.queries.catalog.query(
            """
            SELECT expression FROM alpha
            WHERE instrument_type = ? AND region = ? AND delay = ? AND universe = ?
              AND expression IS NOT NULL
            """,
            scope.params,
        )
        used: set[str] = set()
        for row in rows:
            used |= unique_data_fields(str(row["expression"] or ""))
        if not used:
            return set()

        placeholders = ", ".join("?" for _ in used)
        found = await self.queries.catalog.query(
            f"""
            SELECT DISTINCT dataset_id FROM data_field
            WHERE instrument_type = ? AND region = ? AND delay = ? AND universe = ?
              AND field_id IN ({placeholders})
            """,
            [*scope.params, *sorted(used)],
        )
        return {str(r["dataset_id"]) for r in found if r["dataset_id"]}

    async def plan(
        self,
        *,
        scope: Tuple4,
        target: int = 1000,
        per_dataset: int = 1,
        patterns: list[str] | None = None,
        windows: list[int] | None = None,
        neutralization: str = "SUBINDUSTRY",
    ) -> dict[str, Any]:
        """Fields from datasets this consultant has never worked, ready to run."""
        worked = await self.worked_datasets(scope)
        fresh = await self._fresh_fields(scope, worked, per_dataset, target)

        if not fresh:
            return {
                "harvest": None,
                "fields": [],
                "workedDatasets": len(worked),
                "freshDatasets": 0,
                "note": (
                    "You have already touched every dataset in this market. Try another "
                    "market, or use Combine to get more out of what you have."
                    if worked
                    else "No data has been downloaded for this market yet."
                ),
            }

        harvest = await self.harvester.harvest(
            scope,
            target=target,
            fields=[f["field_id"] for f in fresh],
            patterns=patterns,
            windows=windows,
            neutralization=neutralization,
        )
        log.info(
            "diversify.planned",
            scope=scope.label,
            worked=len(worked),
            fresh_fields=len(fresh),
            simulations=harvest.count,
        )
        return {
            "harvest": harvest,
            "fields": fresh,
            "workedDatasets": len(worked),
            "freshDatasets": len({f["dataset_id"] for f in fresh}),
            "note": (
                f"These come from {len({f['dataset_id'] for f in fresh})} datasets you "
                f"have never used. Being different from your own alphas is the point — "
                "anything too close to them cannot be submitted."
            ),
        }

    async def _fresh_fields(
        self, scope: Tuple4, worked: set[str], per_dataset: int, target: int
    ) -> list[dict[str, Any]]:
        """The best field in each dataset the consultant has not touched."""
        clauses = [
            Tuple4.WHERE,
            "field_type = 'MATRIX'",
            "coverage >= ?",
            "alpha_count >= 1",
        ]
        params: list[Any] = [*scope.params, MIN_COVERAGE]
        if worked:
            clauses.append(f"dataset_id NOT IN ({', '.join('?' for _ in worked)})")
            params.extend(sorted(worked))

        return await self.queries.catalog.query(
            f"""
            SELECT field_id, dataset_id, category_id, description, coverage, alpha_count
            FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY dataset_id ORDER BY coverage DESC NULLS LAST
                ) AS rank_in_dataset
                FROM data_field WHERE {" AND ".join(clauses)}
            )
            WHERE rank_in_dataset <= ?
            ORDER BY coverage DESC NULLS LAST
            LIMIT ?
            """,
            [*params, per_dataset, max(1, target)],
        )
