"""LLM Power Pool Lab: datasets, a model, cores and simulations, then add the task to Tasks.

Nothing here calls the LLM or simulates; the preview shows the exact first prompt.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..brain.settings_schema import resolve_options
from ..db.models import Study, StudyStatus, utcnow
from ..labs import power_pool, search
from ..llm.prompts import PROMPTS
from ..llm.registry import DEFAULT_MODEL
from ..optimize.study import POWER_POOL_SAMPLER
from .deps import State
from .search_lab import account_operators

router = APIRouter(prefix="/api/power-pool-lab", tags=["power-pool-lab"])


class PowerPoolRequest(BaseModel):
    region: str
    delay: int = Field(ge=0, le=1)
    universe: str
    dataset_ids: list[str] = Field(default_factory=list, max_length=50)
    model: str | None = None
    cores: int = Field(default=search.MAX_CORES, ge=1, le=search.MAX_CORES)
    simulations: int = Field(default=0, ge=0, le=search.MAX_SIMULATIONS)


async def _models(state: Any) -> list[dict[str, Any]]:
    """Models whose provider has an enabled Key, richest daily budget first."""
    keys = [k for k in await state.llm.keys.list() if k.enabled]
    out = []
    for m in state.llm.registry.all():
        mine = [k for k in keys if k.provider == m.provider]
        if m.kind == "embedding" or not mine:
            continue
        left = sum([(await state.llm.ledger.headroom(k.id, m)).daily_remaining for k in mine])
        out.append(
            {
                "id": m.id,
                "label": m.label,
                "provider": m.provider,
                "tpm": m.tpm,
                "remainingToday": left,
            }
        )
    return out


@router.get("/options")
async def options(state: State) -> dict[str, Any]:
    models = await _models(state)
    ids = [m["id"] for m in models]
    return {
        "models": models,
        "defaultModel": DEFAULT_MODEL if DEFAULT_MODEL in ids else (ids[0] if ids else None),
        "maxSimulations": search.MAX_SIMULATIONS,
    }


async def _plan(body: PowerPoolRequest, state: Any) -> dict[str, Any]:
    problems: list[str] = []
    warnings: list[str] = []
    operators = await account_operators(state, refresh=False)
    if not operators:
        problems.append("Your BRAIN operators could not be read. Sign in again, then reload.")
    if not body.dataset_ids:
        problems.append("Choose at least one dataset.")
    models = {m["id"]: m for m in await _models(state)}
    model_id = body.model or DEFAULT_MODEL
    info = state.llm.registry.get(model_id)
    if model_id not in models or info is None:
        problems.append(f"{model_id} can't run: no enabled Key for it. Add one in LLM Integration.")

    schema = await state.auth.cached_settings_schema()
    legal = (
        resolve_options(
            schema, {"instrumentType": "EQUITY", "region": body.region, "delay": body.delay}
        )
        if schema
        else {}
    )

    def choices(name: str) -> list[str]:
        return [str(c.get("value")) for c in (legal.get(name, {}).get("choices") or [])]

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
    neutralizations = [n for n in choices("neutralization") if n != "NONE"]
    if not universes:
        problems.append(
            f"No {body.region} delay {body.delay} market is downloaded. Sync it in the Data Explorer."
        )
    if not neutralizations:
        problems.append("BRAIN's settings list is not loaded. Sign in again.")

    fields = 0
    prompt = None
    run = {
        "region": body.region,
        "delay": body.delay,
        "universes": universes,
        "neutralizations": neutralizations,
    }
    if universes:
        for dataset in body.dataset_ids:
            ctx = await power_pool.context_for(
                state.catalog, body.region, body.delay, universes, dataset
            )
            if ctx is None:
                problems.append(
                    f"{dataset} is not in the downloaded {body.region} delay {body.delay} catalog."
                )
                continue
            fields += len(ctx.fields)
            if prompt is None and info is not None and operators:
                user, shown = power_pool.user_prompt(
                    ctx, operators, run, "None yet.", 0, power_pool.budget_for(info)
                )
                system = PROMPTS["power_pool_lab"].body
                tokens = (len(system) + len(user)) // 4
                prompt = {"system": system, "user": user, "tokens": tokens}
                if ctx.fields and shown < min(10, len(ctx.fields)):
                    problems.append(
                        f"The prompt does not fit {info.label}'s tokens per minute. Choose another model."
                    )
    calls = -(-body.simulations // power_pool.PER_CALL)
    if model_id in models and calls > models[model_id]["remainingToday"]:
        warnings.append(
            f"About {calls:,} LLM calls; {model_id} has {models[model_id]['remainingToday']:,} left today, "
            "so the task waits for the reset at midnight Pacific."
        )
    return {
        "fields": fields,
        "universes": universes,
        "neutralizations": neutralizations,
        "llmCalls": calls,
        "prompt": prompt,
        "problems": problems,
        "warnings": warnings,
        "model": model_id,
    }


@router.post("/preview")
async def preview(body: PowerPoolRequest, state: State) -> dict[str, Any]:
    """What a task would send. Free: no LLM call, no simulation."""
    return await _plan(body, state)


@router.post("/tasks", status_code=201)
async def add_task(body: PowerPoolRequest, state: State) -> dict[str, Any]:
    if body.simulations < 1:
        raise HTTPException(
            422,
            detail={"code": "no_simulations", "message": "Assign the simulations for this task."},
        )
    plan = await _plan(body, state)
    if plan["problems"]:
        raise HTTPException(
            422, detail={"code": "power_pool_blocked", "message": plan["problems"][0]}
        )
    now = utcnow()
    task = f"power-pool-{now:%y%m%d%H%M%S%f}"
    row = Study(
        name=f"LLM Power Pool Lab · {task}",
        template_name="LLM Power Pool Lab",
        template_source="# LLM Power Pool Lab writes its expressions with an LLM; there is no template.",
        sampler=POWER_POOL_SAMPLER,
        sampler_params={
            "region": body.region,
            "delay": body.delay,
            "universe": body.universe,
            "universes": plan["universes"],
            "neutralizations": plan["neutralizations"],
            "datasetIds": body.dataset_ids,
            "model": plan["model"],
            "cores": body.cores,
            "llm": {"calls": 0},
            "calls": [],
        },
        objectives=["sharpe"],
        directions=["maximize"],
        batch_size=body.cores * 10,
        max_trials=body.simulations,
        task=task,
        status=StudyStatus.IDLE,
    )
    async with state.db.session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    await state.optimizer._notify()
    return {"id": row.id, "name": row.name}
