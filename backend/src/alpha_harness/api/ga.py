"""Evolution Lab: choose seed Alphas, cores and simulations, then add the breeding to Tasks.

Nothing here runs a search: a task is added not started and is run from Tasks. Auto Select
reads the local store and downloads daily PnL where it is missing; it never simulates.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Literal

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db.models import Study, StudyStatus, Trial, TrialState, utcnow
from ..labs import ga, search, tasks
from ..optimize.study import GA_SAMPLER, _vault_summary
from ..vault.store import SUBMITTED
from .deps import State
from .search_lab import account_operators, neutralizations_for

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/evolution-lab", tags=["evolution-lab"])

POPULATIONS = (50, 100, 200)
MUTATION_RATES = (0.03, 0.05, 0.08)
MAX_SEEDS = 100


class EvolutionRequest(BaseModel):
    region: str
    delay: int = Field(ge=0, le=1)
    universe: str
    alpha_ids: list[str] = Field(default_factory=list, max_length=MAX_SEEDS)
    cores: int = Field(default=search.MAX_CORES, ge=1, le=search.MAX_CORES)
    #: ``None`` sizes the population from the simulations.
    population: Literal[50, 100, 200] | None = None
    mutation_rate: float = Field(default=ga.MUTATION_RATE, ge=0.01, le=0.2)
    #: Needed to add a task; a preview uses it to size the population.
    simulations: int = Field(default=0, ge=0, le=search.MAX_SIMULATIONS)


class AutoSeedsRequest(BaseModel):
    region: str
    delay: int = Field(ge=0, le=1)
    universe: str
    count: int = Field(default=50, ge=2, le=MAX_SEEDS)


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


async def _market(
    state: Any, region: str, delay: int, universe: str
) -> tuple[ga.Market | None, list[str]]:
    """The market children are bred in, and why it cannot be used if it cannot."""
    problems: list[str] = []
    operators = await account_operators(state, refresh=False)
    if not operators:
        problems.append("Your BRAIN operators could not be read. Sign in again, then reload.")
    neutralizations = await neutralizations_for(state, region, delay)
    if not neutralizations:
        problems.append("BRAIN's settings list is not loaded. Sign in again.")
    market = await ga.market_for(state.catalog, operators, region, delay, universe, neutralizations)
    if market is None and operators:
        problems.append(
            f"{region} delay {delay} {universe} is not downloaded. Sync it in the Data Explorer."
        )
    return market, problems


@router.get("/options")
async def options(state: State, refresh: bool = False) -> dict[str, Any]:
    operators = await account_operators(state, refresh=refresh)
    markets = await state.catalog.query(
        f"""
        SELECT a.region, a.delay, a.universe, count(*) AS alphas FROM alpha a
        WHERE coalesce(a.instrument_type, 'EQUITY') = 'EQUITY'
          AND a.region IS NOT NULL AND a.delay IS NOT NULL AND a.universe IS NOT NULL
          AND NOT {SUBMITTED} AND coalesce(a.sim_type, 'REGULAR') = 'REGULAR'
          AND a.expression IS NOT NULL
        GROUP BY 1, 2, 3
        ORDER BY alphas DESC
        """
    )
    return {
        "operators": {"synced": bool(operators), "count": len(operators)},
        "markets": [
            {
                "region": m["region"],
                "delay": int(m["delay"]),
                "universe": m["universe"],
                "alphas": int(m["alphas"]),
            }
            for m in markets
        ],
        "populations": list(POPULATIONS),
        "mutationRates": list(MUTATION_RATES),
        "defaults": {"population": None, "mutationRate": ga.MUTATION_RATE},
        "maxCores": search.MAX_CORES,
        "maxSimulations": search.MAX_SIMULATIONS,
        "maxSeeds": MAX_SEEDS,
    }


async def _plan(body: EvolutionRequest, state: Any) -> dict[str, Any]:
    """Everything a task would breed from, checked, without queueing anything."""
    market, problems = await _market(state, body.region, body.delay, body.universe)
    ids = list(dict.fromkeys(body.alpha_ids))
    rows = await state.alphas.by_ids(ids)
    seeds: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for alpha_id in ids:
        row = rows.get(alpha_id)
        reason = ga.seed_problem(row, body.region, body.delay, body.universe, market)
        if reason:
            skipped.append({"alphaId": alpha_id, "reason": reason})
        else:
            seeds.append(row)
    seeds.sort(key=ga.seed_score, reverse=True)
    if ids and len(seeds) < 2:
        problems.append("Choose at least 2 seeds this market can breed from.")

    sample: list[dict[str, Any]] = []
    if market is not None and len(seeds) >= 2 and not problems:
        rng = random.Random()
        run = {"region": body.region, "delay": body.delay, "universe": body.universe}
        parents = [p for r in seeds if (p := ga.Parent.of(str(r["alpha_id"]), r["expression"], r))]
        for _ in range(50):
            if len(sample) >= 5:
                break
            made = ga.child(
                rng, rng.choice(parents), rng.choice(parents), market, body.mutation_rate
            )
            if made:
                request = ga.request_for(*made, run)
                settings = request.settings.model_dump(by_alias=True, exclude_none=True)
                sample.append({"expression": request.regular, "settings": settings})

    population = ga.population_for(body.simulations, body.population)
    return {
        "seeds": seeds,
        "skipped": skipped,
        "population": population,
        "generations": body.simulations // population,
        "sample": sample,
        "problems": problems,
        "warnings": [],
        "neutralizations": list(market.neutralizations) if market else [],
    }


@router.post("/preview")
async def preview(body: EvolutionRequest, state: State) -> dict[str, Any]:
    """What a task would breed from. Free; queues nothing."""
    plan = await _plan(body, state)
    return {k: v for k, v in plan.items() if k != "neutralizations"} | {
        "seeds": [ga.seed_row(r) for r in plan["seeds"]]
    }


#: Auto Select jobs by id: the task reporting progress, its market and, once done, its result.
_jobs: dict[str, dict[str, Any]] = {}
_running: set[asyncio.Task[None]] = set()


@router.post("/seeds/auto", status_code=202)
async def auto_seeds(body: AutoSeedsRequest, state: State) -> dict[str, Any]:
    """Choose seeds in the background: the best, mutually uncorrelated unsubmitted Alphas."""
    wanted = (body.region, body.delay, body.universe)
    for job_id, job in _jobs.items():
        if job["task"].state == "running":
            if job["market"] == wanted:
                return {"jobId": job_id}
            raise _refuse(409, "busy", "Auto Select is already choosing seeds for another market.")
    market, problems = await _market(state, *wanted)
    if market is None or problems:
        raise _refuse(422, "no_market", (problems or ["This market cannot breed."])[0])

    _jobs.clear()  # Only finished jobs are left, and a new one replaces them.
    task = await state.tasks.start("evolution-seeds", "Choosing seeds")
    job: dict[str, Any] = {"task": task, "market": wanted, "result": None}
    _jobs[task.id] = job

    async def report(kept: int, examined: int) -> None:
        detail = f"{kept} of {body.count} seeds · {examined} examined"
        await state.tasks.update(task, progress=kept / body.count, detail=detail)

    async def run() -> None:
        try:
            found = await ga.auto_seeds(state, market, *wanted, body.count, report)
            job["result"] = {
                "seeds": [ga.seed_row(r) for r in found["rows"]],
                "wanted": body.count,
                "pool": found["pool"],
                "examined": found["examined"],
                "reasons": found["reasons"],
            }
            await state.tasks.finish(task)
        except asyncio.CancelledError:
            await state.tasks.finish(task, state="cancelled")
            raise
        except Exception as exc:
            log.exception("evolution.auto_seeds_failed")
            await state.tasks.finish(task, state="failed", error=str(exc)[:300])

    handle = asyncio.create_task(run(), name="evolution-seeds")
    _running.add(handle)
    handle.add_done_callback(_running.discard)
    return {"jobId": task.id}


@router.get("/seeds/auto/{job_id}")
async def auto_seeds_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise _refuse(404, "unknown_job", "That Auto Select is no longer known. Start it again.")
    task = job["task"]
    return {
        "state": task.state,
        "progress": task.progress,
        "detail": task.detail,
        "error": task.error,
        "result": job["result"],
    }


@router.post("/tasks", status_code=201)
async def add_task(body: EvolutionRequest, state: State) -> dict[str, Any]:
    """Add the breeding to Tasks, not started. It spends nothing until it is run there."""
    if body.simulations < 1:
        raise _refuse(422, "no_simulations", "Assign the simulations for this task.")
    plan = await _plan(body, state)
    if plan["problems"]:
        raise _refuse(422, "evolution_blocked", plan["problems"][0])
    seeds = plan["seeds"]
    if len(seeds) < 2:
        raise _refuse(422, "no_seeds", "Choose at least 2 seeds.")

    now = utcnow()
    task = f"evolution-{now:%y%m%d%H%M%S%f}"
    row = Study(
        name=f"Evolution Lab · {task}",
        template_name="Evolution Lab",
        template_source="# Evolution Lab breeds from seed Alphas; there is no template.",
        sampler=GA_SAMPLER,
        sampler_params={
            "region": body.region,
            "delay": body.delay,
            "universe": body.universe,
            "cores": body.cores,
            "seeds": [str(r["alpha_id"]) for r in seeds],
            "population": plan["population"],
            "mutationRate": body.mutation_rate,
            "testPeriod": ga.TEST_PERIOD,
            "neutralizations": plan["neutralizations"],
        },
        objectives=["train_fitness"],
        directions=["maximize"],
        batch_size=body.cores * 10,
        max_trials=body.simulations,
        task=task,
        status=StudyStatus.IDLE,
    )
    async with state.db.session() as session:
        session.add(row)
        await session.flush()
        # The seeds are generation 0, scored on their stored Fitness: nothing is simulated.
        for number, seed in enumerate(seeds):
            settings = {
                "instrumentType": "EQUITY",
                "region": body.region,
                "delay": body.delay,
                "universe": body.universe,
                "neutralization": seed.get("neutralization"),
                "decay": seed.get("decay"),
                "truncation": seed.get("truncation"),
            }
            session.add(
                Trial(
                    study_id=row.id,
                    number=number,
                    params={"parents": [], "generation": 0},
                    distributions={},
                    expression=seed["expression"],
                    settings={k: v for k, v in settings.items() if v is not None},
                    state=TrialState.COMPLETE,
                    values=[float(seed["fitness"])],
                    result=_vault_summary(seed),
                    alpha_id=str(seed["alpha_id"]),
                    generation=0,
                    message=tasks.FREE,
                    finished_at=now,
                )
            )
        await session.commit()
        await session.refresh(row)
    await state.optimizer._notify()
    return {"id": row.id, "name": row.name}
