"""Tasks: what the labs added, run from one place.

A lab only adds a task. Here a task runs: it waits until its cores fit in the free slots,
runs until its simulations are spent, across days if it has to, and can be paused,
stopped, changed or removed. Search Lab, Template Lab and Evolution Lab add tasks.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from ..db.models import Study, StudyStatus, Trial, TrialState, utcnow
from ..labs import search, tasks
from ..optimize.errors import StudyNotFoundError
from ..optimize.objectives import OBJECTIVES
from ..optimize.study import TASK_SAMPLERS, TEMPLATE_SAMPLER, ranked
from .deps import State

router = APIRouter(prefix="/api/lab-tasks", tags=["lab-tasks"])

#: Where a task can be run from: never started, or paused.
RUNNABLE = (StudyStatus.IDLE, StudyStatus.PAUSED)
FINISHED = (StudyStatus.COMPLETE, StudyStatus.FAILED)


class TaskChange(BaseModel):
    cores: int | None = Field(default=None, ge=1, le=search.MAX_CORES)
    simulations: int | None = Field(default=None, ge=1, le=search.MAX_SIMULATIONS)


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


async def _rows(state: Any) -> list[Study]:
    async with state.db.session() as session:
        return list(
            (
                await session.scalars(
                    select(Study).where(Study.sampler.in_(TASK_SAMPLERS)).order_by(Study.id.desc())
                )
            ).all()
        )


async def _one(state: Any, task_id: int) -> Study:
    row = await state.optimizer.get(task_id)
    if row is None or row.sampler not in TASK_SAMPLERS:
        raise StudyNotFoundError(task_id)
    return row


async def _progress(state: Any, ids: list[int]) -> dict[int, dict[str, Any]]:
    """Trials by state, trials answered for free, and the best value per task, counted in SQL.

    A long task holds tens of thousands of trials; reading them all to count them would
    slow the list down with every day the task runs.
    """
    out: dict[int, dict[str, Any]] = {i: {"states": {}, "free": 0, "best": None} for i in ids}
    if not ids:
        return out
    value = func.json_extract(Trial.values, "$[0]")
    async with state.db.session() as session:
        states = await session.execute(
            select(Trial.study_id, Trial.state, func.count())
            .where(Trial.study_id.in_(ids))
            .group_by(Trial.study_id, Trial.state)
        )
        for study_id, trial_state, n in states.all():
            out[study_id]["states"][str(trial_state)] = int(n)
        free = await session.execute(
            select(Trial.study_id, func.count())
            .where(
                Trial.study_id.in_(ids),
                Trial.state.in_([TrialState.COMPLETE, TrialState.FAIL]),
                Trial.message == tasks.FREE,
            )
            .group_by(Trial.study_id)
        )
        for study_id, n in free.all():
            out[study_id]["free"] = int(n)
        # An Alpha that returned no value is scored at the failure value, so it is not a best;
        # nor is a seed, which was scored before the task began.
        best = await session.execute(
            select(Trial.study_id, func.max(value))
            .where(
                Trial.study_id.in_(ids),
                Trial.state == TrialState.COMPLETE,
                value > OBJECTIVES["sharpe"].failure_value,
                or_(Trial.generation.is_(None), Trial.generation != 0),
            )
            .group_by(Trial.study_id)
        )
        for study_id, value in best.all():
            out[study_id]["best"] = None if value is None else float(value)
    return out


def _task(row: Study, progress: dict[str, Any]) -> dict[str, Any]:
    params = row.sampler_params or {}
    states = progress["states"]
    told = states.get(TrialState.COMPLETE, 0) + states.get(TrialState.FAIL, 0)
    template = row.sampler == TEMPLATE_SAMPLER
    objective = OBJECTIVES.get((row.objectives or ["sharpe"])[0], OBJECTIVES["sharpe"])
    return {
        "id": row.id,
        "lab": row.sampler,
        "labName": TASK_SAMPLERS.get(row.sampler, row.sampler),
        "templateName": row.template_name if template else None,
        "template": row.template_source if template else None,
        "status": row.status,
        "stopping": bool(params.get("stopping")),
        "message": row.message,
        "region": params.get("region"),
        "delay": params.get("delay"),
        "universe": params.get("universe"),
        "seeds": len(params.get("seeds") or []),
        "population": params.get("population"),
        "mutationRate": params.get("mutationRate"),
        "decay": params.get("decay"),
        "cores": tasks.cores_of(row),
        "datasetIds": params.get("datasetIds") or [],
        "fields": len((params.get("space") or {}).get("fields") or {}),
        "target": row.max_trials,
        "simulated": told - progress["free"],
        "queued": states.get(TrialState.QUEUED, 0),
        "running": states.get(TrialState.RUNNING, 0),
        "failed": states.get(TrialState.FAIL, 0),
        "best": progress["best"],
        "objectiveLabel": objective.label,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
    }


async def _payload(state: Any, task_id: int) -> dict[str, Any]:
    row = await _one(state, task_id)
    return _task(row, (await _progress(state, [row.id]))[row.id])


@router.get("")
async def list_tasks(state: State) -> dict[str, Any]:
    """Every task, newest first, and the slots they share."""
    rows = await _rows(state)
    progress = await _progress(state, [r.id for r in rows])
    return {"slots": state.engine.slots, "tasks": [_task(r, progress[r.id]) for r in rows]}


async def _queue(state: Any, ids: list[int]) -> None:
    """Hand tasks to the scheduler. Each starts as soon as its cores fit."""
    queued_at = utcnow().isoformat()
    async with state.db.session() as session:
        for task_id in ids:
            row = await session.get(Study, task_id)
            if row is not None and row.status in RUNNABLE:
                row.status = StudyStatus.QUEUED
                row.sampler_params = {**(row.sampler_params or {}), "queuedAt": queued_at}
        await session.commit()
    await tasks.start_waiting(state.optimizer)
    await state.optimizer._notify()


@router.post("/run-all")
async def run_all(state: State) -> dict[str, Any]:
    """Run every task not started yet, oldest first. What does not fit waits its turn."""
    fresh = sorted(r.id for r in await _rows(state) if r.status == StudyStatus.IDLE)
    await _queue(state, fresh)
    return await list_tasks(state)


@router.post("/{task_id}/run")
async def run(task_id: int, state: State) -> dict[str, Any]:
    """Run a task, or resume a paused one. It starts once its cores fit."""
    row = await _one(state, task_id)
    if row.status not in RUNNABLE:
        raise _refuse(409, "not_runnable", "Only a task not started or paused can run.")
    await _queue(state, [task_id])
    return await _payload(state, task_id)


@router.post("/{task_id}/pause")
async def pause(task_id: int, state: State) -> dict[str, Any]:
    """Queue nothing more for now. Simulations already sent finish; the rest come off."""
    row = await _one(state, task_id)
    stopping = bool((row.sampler_params or {}).get("stopping"))
    if row.status not in (StudyStatus.RUNNING, StudyStatus.QUEUED) or stopping:
        raise _refuse(409, "not_running", "Only a running or waiting task can pause.")
    async with state.optimizer._lock(task_id):
        await state.optimizer.set_status(task_id, StudyStatus.PAUSED)
        await state.engine.drop_queued(row.task)
        await tasks.prune_unsent(state.optimizer, task_id)
    await tasks.start_waiting(state.optimizer)
    return await _payload(state, task_id)


@router.post("/{task_id}/stop")
async def stop(task_id: int, state: State) -> dict[str, Any]:
    """Finish a task early. Simulations already sent still finish and are scored."""
    row = await _one(state, task_id)
    if row.status not in (StudyStatus.RUNNING, StudyStatus.PAUSED, StudyStatus.QUEUED):
        raise _refuse(409, "not_started", "Only a task that has been run can stop.")
    async with state.optimizer._lock(task_id):
        await state.engine.drop_queued(row.task)
        await tasks.prune_unsent(state.optimizer, task_id)
        counts = await state.optimizer.counts(task_id)
        out = counts.get(TrialState.QUEUED, 0) + counts.get(TrialState.RUNNING, 0)
        async with state.db.session() as session:
            stored = await session.get(Study, task_id)
            if stored is not None:
                stored.sampler_params = {**(stored.sampler_params or {}), "stopping": True}
                # With simulations still out it runs on only to score them.
                if out:
                    stored.status = StudyStatus.RUNNING
                else:
                    stored.status = StudyStatus.COMPLETE
                    stored.finished_at = utcnow()
                await session.commit()
    await tasks.start_waiting(state.optimizer)
    await state.optimizer._notify()
    return await _payload(state, task_id)


@router.patch("/{task_id}")
async def change(task_id: int, body: TaskChange, state: State) -> dict[str, Any]:
    """Change a task's cores or simulations. A running task takes them from its next round."""
    row = await _one(state, task_id)
    if row.status in FINISHED:
        raise _refuse(409, "finished", "A finished task can't change.")
    before = tasks.cores_of(row)
    cores = body.cores or before
    async with state.optimizer._lock(task_id):
        if row.status == StudyStatus.RUNNING and cores > before:
            rows = await _rows(state)
            used = sum(tasks.cores_of(r) for r in rows if r.status == StudyStatus.RUNNING)
            free = max(0, state.engine.slots - used)
            if cores - before > free:
                raise _refuse(409, "no_cores", f"Only {free} more cores are free right now.")
        async with state.db.session() as session:
            stored = await session.get(Study, task_id)
            if stored is None:
                raise StudyNotFoundError(task_id)
            stored.batch_size = cores * 10
            if body.simulations is not None:
                stored.max_trials = body.simulations
            stored.sampler_params = {**(stored.sampler_params or {}), "cores": cores}
            await session.commit()
        await state.engine.set_quota(row.task, cores)
    await state.optimizer._notify()
    return await _payload(state, task_id)


@router.delete("/{task_id}")
async def remove(task_id: int, state: State) -> dict[str, Any]:
    """Remove a task that is not running. The Alphas it found stay in Alphas."""
    row = await _one(state, task_id)
    counts = await state.optimizer.counts(task_id)
    if row.status == StudyStatus.RUNNING or counts.get(TrialState.RUNNING, 0):
        raise _refuse(409, "running", "Pause or stop the task first: simulations are still out.")
    await state.engine.drop_queued(row.task)
    await state.optimizer.delete(task_id)
    await state.engine.set_quota(row.task, 0, enabled=False)
    await state.optimizer._notify()
    return {"removed": task_id}


@router.get("/{task_id}/top")
async def top(
    task_id: int, state: State, limit: int = Query(20, ge=1, le=200)
) -> list[dict[str, Any]]:
    """The task's best Alphas on what it searches for. Seeds are not among them."""
    row = await _one(state, task_id)
    async with state.db.session() as session:
        best = list(
            (
                await session.scalars(
                    select(Trial)
                    .where(
                        Trial.study_id == task_id,
                        Trial.state == TrialState.COMPLETE,
                        or_(Trial.generation.is_(None), Trial.generation != 0),
                    )
                    .order_by(func.json_extract(Trial.values, "$[0]").desc())
                    .limit(limit)
                )
            ).all()
        )
    return ranked(best, row.directions)
