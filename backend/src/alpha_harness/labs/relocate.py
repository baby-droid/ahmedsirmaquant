"""Relocate: run an idea that works somewhere else.

The cheapest good alpha in the product. An expression that earns in USA/TOP3000 is not
guaranteed to earn in EUR/TOP2500, but it costs one simulation to find out and the
formula does not change at all — no new idea, no new fields, no search. It is the only
lab whose input is something the consultant already has and whose output can be as good
as anything they have produced.

It is orthogonal to every other lab because it holds the *expression* fixed and moves
only the settings: market, universe, grouping, delay. Everything else in this product
holds settings fixed and moves the expression.

**The one thing that makes it non-trivial.** Not every data field exists in every
(instrument type, region, delay, universe). A field present in USA may simply be absent
in EUR, and simulating there fails and spends the allowance to learn nothing. So every
candidate destination is checked field-by-field against the synced catalog first, and a
scope missing any field is refused rather than attempted. That check is the lab.

**Neutralization is region-dependent** and is not inferred here. The caller supplies the
groupings to try; the platform's own settings schema is the authority on which are legal
where, and it is already cached at sign-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from ..brain.schemas import SimulationRequest, SimulationSettings
from ..catalog.queries import CatalogQueries, Tuple4
from ..templates.validate import unique_data_fields

log = structlog.get_logger(__name__)

#: Below this an alpha is not worth carrying to another market — relocating a weak
#: signal mostly produces another weak signal, at the same cost as relocating a strong one.
DEFAULT_MIN_SHARPE = 1.0

#: A ceiling on one relocation, so a large pool cannot queue the entire day by accident.
MAX_SIMULATIONS = 5_000


@dataclass(slots=True)
class Move:
    """One alpha, one destination, and whether it can actually go there."""

    alpha_id: str
    expression: str
    origin: str
    destination: Tuple4
    neutralization: str
    possible: bool
    missing: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "alphaId": self.alpha_id,
            "expression": self.expression,
            "origin": self.origin,
            "destination": self.destination.label,
            "neutralization": self.neutralization,
            "possible": self.possible,
            "missing": self.missing,
            "why": (
                f"The same formula, judged against {self.destination.region} instead."
                if self.possible
                else f"Not possible here — {', '.join(self.missing[:3])} does not exist "
                f"in {self.destination.region}."
            ),
        }


class Relocator:
    """Takes what already works and points it somewhere else."""

    def __init__(self, queries: CatalogQueries, alphas: Any) -> None:
        self.queries = queries
        self.alphas = alphas

    async def destinations(self, origin: Tuple4) -> list[Tuple4]:
        """Every synced scope that is not where we already are."""
        rows = await self.queries.synced_tuples()
        out = []
        for row in rows:
            scope = Tuple4(
                instrument_type=row["instrument_type"],
                region=row["region"],
                delay=int(row["delay"]),
                universe=row["universe"],
            )
            if scope.label != origin.label:
                out.append(scope)
        return out

    async def plan(
        self,
        *,
        origin: Tuple4,
        min_sharpe: float = DEFAULT_MIN_SHARPE,
        limit: int = 20,
        neutralizations: list[str] | None = None,
        target: int = 500,
    ) -> dict[str, Any]:
        """Which of this consultant's alphas can travel, and where.

        Costs nothing — every check is against the local catalog. The preview and the
        run share this, so what is shown is exactly what would be queued.
        """
        groupings = neutralizations or ["SUBINDUSTRY"]
        destinations = await self.destinations(origin)
        if not destinations:
            return {
                "moves": [],
                "requests": [],
                "note": (
                    "Only one market has been downloaded, so there is nowhere else to try "
                    "yet. Download a second market and this becomes the cheapest lab you have."
                ),
            }

        travellers = await self._worth_moving(origin, min_sharpe, limit)
        if not travellers:
            return {
                "moves": [],
                "requests": [],
                "note": (
                    f"Nothing in {origin.label} has scored well enough to be worth "
                    "carrying elsewhere yet. Run a day first, then come back."
                ),
            }

        moves: list[Move] = []
        requests: list[SimulationRequest] = []
        for alpha in travellers:
            expression = str(alpha["expression"] or "")
            if not expression:
                continue
            # Grouping fields (industry, sector, market) are available everywhere and are
            # not rows in the field catalog, so checking for them would block every move.
            wanted = unique_data_fields(expression)

            for destination in destinations:
                missing = await self._missing_fields(wanted, destination)
                for grouping in groupings:
                    move = Move(
                        alpha_id=str(alpha["alpha_id"]),
                        expression=expression,
                        origin=origin.label,
                        destination=destination,
                        neutralization=grouping,
                        possible=not missing,
                        missing=sorted(missing),
                    )
                    moves.append(move)
                    if move.possible and len(requests) < min(target, MAX_SIMULATIONS):
                        requests.append(_request(expression, destination, grouping, alpha))

        possible = sum(1 for m in moves if m.possible)
        log.info(
            "relocate.planned",
            origin=origin.label,
            alphas=len(travellers),
            destinations=len(destinations),
            possible=possible,
            blocked=len(moves) - possible,
        )
        return {
            "moves": [m.to_dict() for m in moves],
            "requests": requests,
            "alphas": len(travellers),
            "destinations": [d.label for d in destinations],
            "possible": possible,
            "blocked": len(moves) - possible,
            "note": None
            if possible
            else (
                "None of these formulas can run in the other markets — they use data "
                "those markets do not have. This is normal for fundamental data."
            ),
        }

    # -- helpers ---------------------------------------------------------

    async def _worth_moving(
        self, origin: Tuple4, min_sharpe: float, limit: int
    ) -> list[dict[str, Any]]:
        """The alphas from this scope good enough to be worth carrying."""
        return await self.queries.catalog.query(
            """
            SELECT alpha_id, expression, sharpe, fitness, neutralization
            FROM alpha
            WHERE instrument_type = ? AND region = ? AND delay = ? AND universe = ?
              AND expression IS NOT NULL
              AND sharpe IS NOT NULL AND abs(sharpe) >= ?
            ORDER BY abs(sharpe) DESC
            LIMIT ?
            """,
            [*origin.params, min_sharpe, limit],
        )

    async def _missing_fields(self, wanted: set[str], destination: Tuple4) -> set[str]:
        """Which of these fields the destination does not have.

        Checked against the catalog rather than assumed: a simulation launched against a
        field that is not there fails, and a failed simulation costs exactly as much
        allowance as a successful one.
        """
        if not wanted:
            return set()
        placeholders = ", ".join("?" for _ in wanted)
        rows = await self.queries.catalog.query(
            f"""
            SELECT DISTINCT field_id FROM data_field
            WHERE instrument_type = ? AND region = ? AND delay = ? AND universe = ?
              AND field_id IN ({placeholders})
            """,
            [*destination.params, *sorted(wanted)],
        )
        return set(wanted) - {str(r["field_id"]) for r in rows}


def _request(
    expression: str, destination: Tuple4, neutralization: str, alpha: dict[str, Any]
) -> SimulationRequest:
    return SimulationRequest(
        type="REGULAR",
        settings=SimulationSettings(
            instrumentType=destination.instrument_type,
            region=destination.region,
            delay=destination.delay,
            universe=destination.universe,
            neutralization=neutralization,
            decay=0,
            truncation=0.08,
        ),
        regular=expression,
    )
