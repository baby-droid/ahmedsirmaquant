"""Search Lab: choose datasets, cores and simulations, then add the search to Tasks.

Nothing here runs a search. A task is added not started and is run from Tasks, where it
waits for free cores and may use more than one day's allowance before it is done.
"""

from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..brain.settings_schema import resolve_options
from ..db.models import Study, StudyStatus, utcnow
from ..labs import search
from ..optimize.study import SEARCH_SAMPLER
from .deps import State

router = APIRouter(prefix="/api/search-lab", tags=["search-lab"])


class SearchRequest(BaseModel):
    region: str
    delay: int = Field(ge=0, le=1)
    #: The chosen market's universe; searched first, other universes join if they overlap.
    universe: str | None = None
    dataset_ids: list[str] = Field(default_factory=list, max_length=200)
    vector_operators: list[str] = Field(default_factory=list)
    decay: int = 0
    cores: int = Field(default=search.MAX_CORES, ge=1, le=search.MAX_CORES)
    #: Needed to add a task; a preview ignores it.
    simulations: int = Field(default=0, ge=0, le=search.MAX_SIMULATIONS)


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


async def account_operators(state: Any, *, refresh: bool) -> list[dict[str, Any]]:
    """The account's own operators, fetched from BRAIN when missing or asked for."""
    cached = await state.auth.cached_operators()
    if refresh or not cached:
        try:
            return await state.auth.refresh_operators()
        except Exception:
            return cached or []
    return cached


async def neutralizations_for(state: Any, region: str, delay: int) -> list[str]:
    """The neutralizations a lab uses in a market: BRAIN's legal ones, in the lab's order."""
    schema = await state.auth.cached_settings_schema()
    if not schema:
        return []
    legal = resolve_options(schema, {"instrumentType": "EQUITY", "region": region, "delay": delay})
    offered = [c.get("value") for c in (legal.get("neutralization", {}).get("choices") or [])]
    return [n for n in search.NEUTRALIZATIONS if n in offered] or offered[:1]


@router.get("/options")
async def options(state: State, refresh: bool = False) -> dict[str, Any]:
    operators = await account_operators(state, refresh=refresh)
    cat = search.catalogue(operators)
    return {
        "operators": {"synced": bool(operators), "count": len(operators)},
        "crossSectional": list(cat.cs),
        "timeSeries": list(cat.ts),
        "group": list(cat.group),
        "vector": list(cat.vector),
        "lookbacks": list(search.LOOKBACKS),
        "groups": list(search.GROUPS),
        "decays": list(search.DECAYS),
        "truncation": search.TRUNCATION,
        "maxCores": search.MAX_CORES,
        "maxSimulations": search.MAX_SIMULATIONS,
    }


async def market_for(body: SearchRequest, state: Any, need: tuple[str, ...] = ()) -> dict[str, Any]:
    """The market a task searches, checked: universes, fields, neutralizations, vector operators.

    Both labs start here. ``need`` names the fixed fields a template reads; a universe that
    does not have one is left out, and that is said as a warning rather than left unsaid.
    """
    problems: list[str] = []
    warnings: list[str] = []

    operators = await account_operators(state, refresh=False)
    if not operators:
        problems.append("Your BRAIN operators could not be read. Sign in again, then reload.")
    elif "ts_backfill" not in {o.get("name") for o in operators}:
        problems.append("ts_backfill is not available on this account, so fields can't be cleaned.")
    if body.decay not in search.DECAYS:
        problems.append(f"Decay must be one of {', '.join(map(str, search.DECAYS))}.")

    schema = await state.auth.cached_settings_schema()
    if not schema:
        problems.append("BRAIN's settings list is not loaded. Sign in again.")
    market = {"instrumentType": "EQUITY", "region": body.region, "delay": body.delay}
    legal = resolve_options(schema, market) if schema else {}

    def choices(name: str) -> list[Any]:
        return [c.get("value") for c in (legal.get(name, {}).get("choices") or [])]

    synced = {
        str(r["universe"])
        for r in await state.queries.synced_tuples()
        if r["instrument_type"] == "EQUITY"
        and r["region"] == body.region
        and int(r["delay"]) == body.delay
    }
    universes = [u for u in choices("universe") if u in synced]
    if body.universe in universes:
        universes.remove(body.universe)
        universes.insert(0, body.universe)
    downloaded = bool(universes)
    neutralizations = await neutralizations_for(state, body.region, body.delay)
    if schema and not neutralizations:
        problems.append(f"BRAIN offers no neutralization for {body.region}.")

    lacking: set[str] = set()
    if need and universes:
        held: dict[str, set[str]] = {}
        for name in need:
            held[name] = {
                str(r["universe"])
                for r in await state.queries.field_availability(name)
                if r["region"] == body.region and int(r["delay"]) == body.delay
            }
        for universe in list(universes):
            short = [name for name in need if universe not in held[name]]
            if short:
                universes.remove(universe)
                lacking.update(short)
                verb = "is" if len(short) == 1 else "are"
                warnings.append(f"{', '.join(short)} {verb} not in {universe}, so it is left out.")

    vector_ops = [
        v for v in dict.fromkeys(body.vector_operators) if v in search.catalogue(operators).vector
    ]
    pool = search.Pool({}, (), {}, 0)
    if not body.dataset_ids:
        problems.append("Choose at least one dataset.")
    elif not downloaded:
        problems.append(
            f"No {body.region} delay {body.delay} market is downloaded. "
            "Sync it in the Data Explorer."
        )
    elif not universes:
        names = ", ".join(sorted(lacking) or need)
        problems.append(f"No downloaded {body.region} delay {body.delay} universe has {names}.")
    else:
        pool = await search.field_pool(
            state.queries,
            region=body.region,
            delay=body.delay,
            universes=universes,
            dataset_ids=body.dataset_ids,
            allow_vector=bool(vector_ops),
        )
        if not pool.fields:
            problems.append(
                "The chosen datasets have no usable fields in this market."
                + (
                    " Allow a vector operator to use their vector fields."
                    if pool.vector_skipped
                    else ""
                )
            )
    if pool.vector_skipped and pool.fields:
        warnings.append(
            f"{pool.vector_skipped:,} vector fields are left out; "
            "allow a vector operator to use them."
        )
    return {
        "operators": operators,
        "pool": pool,
        "neutralizations": neutralizations,
        "vector": vector_ops,
        "problems": problems,
        "warnings": warnings,
    }


async def _plan(body: SearchRequest, state: Any) -> dict[str, Any]:
    """Everything a task would search, checked, without queueing anything."""
    market = await market_for(body, state)
    problems, pool = market["problems"], market["pool"]
    cat = search.catalogue(market["operators"])
    shapes = search.families(cat)
    if market["operators"] and not shapes:
        problems.append("None of your operators fit a search shape.")

    space = {
        "fields": pool.fields,
        "families": list(shapes),
        "cs": list(cat.cs),
        "ts": list(cat.ts),
        "group": list(cat.group),
        "vector": market["vector"],
        "lookbacks": list(search.LOOKBACKS),
        "groups": list(search.GROUPS),
        "universes": list(pool.universes),
        "absent": pool.absent,
        "neutralizations": market["neutralizations"],
    }
    sample: list[dict[str, Any]] = []
    if not problems:
        rng = random.Random()
        run = {"region": body.region, "delay": body.delay, "decay": body.decay}
        choices = search.field_choices(space)
        for _ in range(5):
            point = search.suggest(search.RandomTrial(rng), space, choices)
            request = search.request_for(point, run)
            settings = request.settings.model_dump(by_alias=True, exclude_none=True)
            sample.append({"expression": request.regular, "settings": settings})

    matrix = sum(1 for t in pool.fields.values() if t == "MATRIX")
    return {
        "round": body.cores * 10,
        "fields": {
            "total": len(pool.fields),
            "matrix": matrix,
            "vector": len(pool.fields) - matrix,
        },
        "leftOut": {"vector": pool.vector_skipped},
        "universes": list(pool.universes),
        "neutralizations": market["neutralizations"],
        "families": list(shapes),
        "sample": sample,
        "problems": problems,
        "warnings": market["warnings"],
        "space": space,
    }


@router.post("/preview")
async def preview(body: SearchRequest, state: State) -> dict[str, Any]:
    """What a task would search. Free; queues nothing."""
    return {k: v for k, v in (await _plan(body, state)).items() if k != "space"}


def startup_trials(fields: int, size: int, per_round: int) -> int:
    """Random draws before the search steers: one per field, at most half the task."""
    cover = min(fields, size // 2)
    return max(per_round, -(-cover // per_round) * per_round)


@router.post("/tasks", status_code=201)
async def add_task(body: SearchRequest, state: State) -> dict[str, Any]:
    """Add the search to Tasks, not started. It spends nothing until it is run there."""
    if body.simulations < 1:
        raise _refuse(422, "no_simulations", "Assign the simulations for this task.")
    plan = await _plan(body, state)
    if plan["problems"]:
        raise _refuse(422, "search_blocked", plan["problems"][0])

    now = utcnow()
    per_round, size = plan["round"], body.simulations
    task = f"search-{now:%y%m%d%H%M%S%f}"
    row = Study(
        name=f"Search Lab · {task}",
        template_name="Search Lab",
        template_source="# Search Lab writes its own expressions; there is no template.",
        sampler=SEARCH_SAMPLER,
        sampler_params={
            "space": plan["space"],
            "region": body.region,
            "delay": body.delay,
            "decay": body.decay,
            "cores": body.cores,
            "datasetIds": body.dataset_ids,
            "n_startup_trials": startup_trials(len(plan["space"]["fields"]), size, per_round),
            "multivariate": True,
            "group": True,
        },
        objectives=["sharpe"],
        directions=["maximize"],
        batch_size=per_round,
        max_trials=size,
        task=task,
        status=StudyStatus.IDLE,
    )
    async with state.db.session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    await state.optimizer._notify()
    return {"id": row.id, "name": row.name}
