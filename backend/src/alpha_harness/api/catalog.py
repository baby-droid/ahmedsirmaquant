"""Data Explorer: syncing the catalog and querying it."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from ..brain.settings_schema import valid_values
from ..catalog.pyramids import pyramid_grid
from ..catalog.queries import FieldFilter, Tuple4
from ..catalog.sync import SyncTarget, serialise_run
from .deps import State

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def scope(
    region: Annotated[str, Query(description="e.g. USA, EUR, GLB")],
    delay: Annotated[int, Query(ge=0, le=1)],
    universe: Annotated[str, Query(description="e.g. TOP3000")],
    instrument_type: Annotated[str, Query(alias="instrumentType")] = "EQUITY",
) -> Tuple4:
    """The (instrumentType, region, delay, universe) scope every query needs."""
    return Tuple4(instrument_type=instrument_type, region=region, delay=delay, universe=universe)


Scope = Annotated[Tuple4, Depends(scope)]


# --- syncing --------------------------------------------------------------


class SyncRequest(BaseModel):
    region: str
    delay: int
    universe: str
    instrument_type: str = "EQUITY"
    restart: bool = False


@router.post("/sync")
async def start_sync(payload: SyncRequest, state: State) -> dict[str, Any]:
    """Download one scope into the local catalog.

    Runs in the background; progress arrives over the WebSocket ``sync`` topic.
    ``restart`` wipes the scope first.
    """
    target = SyncTarget(
        instrument_type=payload.instrument_type,
        region=payload.region,
        delay=payload.delay,
        universe=payload.universe,
    )
    run = await state.sync.start(target, restart=payload.restart)
    return serialise_run(run)


async def _markets(state: State) -> list[SyncTarget]:
    """Every EQUITY market the account can simulate, from BRAIN's own settings schema.

    A new region or universe is picked up without a code change.
    """
    schema = await state.auth.cached_settings_schema() or await state.auth.refresh_metadata()
    # ponytail: EQUITY only, the one instrument type the Data Explorer offers.
    base: dict[str, Any] = {"instrumentType": "EQUITY"}
    return [
        SyncTarget(instrument_type="EQUITY", region=region, delay=int(delay), universe=universe)
        for region in valid_values(schema, "region", base)
        # ponytail: ALL left out for now. BRAIN pages it 50 fields at a time, ~1,700 requests
        # at 30 a minute; drop this line (and the frontend's HIDDEN_REGIONS) to bring it back.
        if region != "ALL"
        for delay in valid_values(schema, "delay", {**base, "region": region})
        for universe in valid_values(schema, "universe", {**base, "region": region, "delay": delay})
    ]


@router.get("/markets")
async def markets(state: State) -> list[dict[str, Any]]:
    """Every market a full sync covers: what the Data Explorer's sync matrix draws."""
    return [
        {
            "instrumentType": t.instrument_type,
            "region": t.region,
            "delay": t.delay,
            "universe": t.universe,
        }
        for t in await _markets(state)
    ]


@router.post("/sync-all")
async def start_sync_all(state: State) -> dict[str, Any]:
    """Download every market BRAIN offers: all fields first, then dataset details.

    Runs in the background; progress, including each market's state, arrives over the
    WebSocket ``sync`` topic.
    """
    targets = await _markets(state)
    if not targets:
        raise HTTPException(
            503,
            detail={
                "code": "no_markets",
                "message": "BRAIN's settings list no markets to download. Sign in again and retry.",
            },
        )
    return await state.sync.start_all(targets)


@router.get("/sync/runs")
async def list_runs(state: State, limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    return [serialise_run(r) for r in await state.sync.runs(limit)]


@router.get("/sync/runs/{run_id}")
async def get_run(run_id: int, state: State) -> dict[str, Any]:
    run = await state.sync.get_run(run_id)
    if run is None:
        raise HTTPException(404, "No such sync run")
    return serialise_run(run)


@router.post("/sync/runs/{run_id}/cancel")
async def cancel_run(run_id: int, state: State) -> dict[str, bool]:
    return {"cancelled": await state.sync.cancel(run_id)}


@router.get("/sync/last")
async def last_sync(state: State) -> list[dict[str, Any]]:
    """Most recent successful sync per scope — the 'last synced' display."""
    return await state.sync.last_sync_by_tuple()


@router.get("/pyramids")
async def pyramids(state: State) -> dict[str, Any]:
    """Every pyramid: its multiplier, this quarter's alpha count, and download state."""
    return await pyramid_grid(state.endpoints, state.catalog)


@router.get("/scopes")
async def scopes(state: State) -> list[dict[str, Any]]:
    """Which scopes hold data locally, and how much."""
    return await state.queries.synced_tuples()


# --- reading --------------------------------------------------------------


@router.get("/counts")
async def counts(scope: Scope, state: State) -> dict[str, int]:
    """Datasets / categories / subcategories / fields for one scope."""
    return await state.queries.counts(scope)


@router.get("/stats")
async def stats(scope: Scope, state: State) -> dict[str, Any]:
    """Value ranges, so filter controls can bound themselves to real data."""
    return await state.queries.stats(scope)


@router.get("/tree")
async def tree(scope: Scope, state: State) -> list[dict[str, Any]]:
    """Category -> subcategory hierarchy with dataset and field counts."""
    return await state.queries.category_tree(scope)


#: Module-level singleton so the default is not rebuilt per request.
DEFAULT_FIELD_FILTER = FieldFilter()


@router.post("/fields")
async def fields(
    scope: Scope,
    state: State,
    filters: Annotated[FieldFilter, Body()] = DEFAULT_FIELD_FILTER,
) -> dict[str, Any]:
    """Filtered, sorted, paginated data fields.

    POST rather than GET because the filter set is large and structured; the operation
    is still a pure read.
    """
    return await state.queries.fields(scope, filters)


@router.post("/facets")
async def facets(
    scope: Scope,
    state: State,
    filters: Annotated[FieldFilter, Body()] = DEFAULT_FIELD_FILTER,
) -> dict[str, list[dict[str, Any]]]:
    """Categories / subcategories / datasets / types with counts under the other filters."""
    return await state.queries.facets(scope, filters)


@router.get("/fields/{field_id}")
async def field_detail(field_id: str, scope: Scope, state: State) -> dict[str, Any]:
    row = await state.queries.field(scope, field_id)
    if row is None:
        raise HTTPException(404, f"{field_id} is not in the catalog for {scope.label}")
    return row


@router.get("/fields/{field_id}/availability")
async def field_availability(field_id: str, state: State) -> list[dict[str, Any]]:
    """Every scope this field exists in.

    Not every field is available in every region/delay/universe, so this is the check
    that stops a template being expanded into simulations that cannot run.
    """
    return await state.queries.field_availability(field_id)


@router.get("/datasets")
async def datasets(scope: Scope, state: State, search: str | None = None) -> list[dict[str, Any]]:
    return await state.queries.datasets(scope, search)


class CoverageRequest(BaseModel):
    field_ids: list[str]


@router.post("/coverage-matrix")
async def coverage_matrix(payload: CoverageRequest, state: State) -> list[dict[str, Any]]:
    """Availability grid for a set of fields across every synced scope."""
    return await state.queries.coverage_matrix(payload.field_ids)
