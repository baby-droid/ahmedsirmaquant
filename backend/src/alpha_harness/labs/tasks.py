"""Tasks: what every research lab shares once its work is added.

A lab adds a task and the Tasks tab runs it. The running is the same for every lab:
waiting for cores, keeping them full as batches come back, and taking unsent work off the
queue. A lab only decides how one point of its search is drawn (its ``draw``), so Search
Lab and Template Lab differ in the Alphas they write, not in how those are run.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import func, select

from ..brain.schemas import SimulationRequest
from ..db.models import SimStatus, SimulationRecord, Study, StudyStatus, Trial, TrialState, utcnow
from ..optimize.study import GA_SAMPLER, POWER_POOL_SAMPLER, TASK_SAMPLERS, TEMPLATE_SAMPLER
from . import ga, search

if TYPE_CHECKING:  # pragma: no cover
    import optuna

    from ..optimize.study import Optimizer

log = structlog.get_logger(__name__)

#: Marks a trial answered from an Alpha already simulated: it spent no quota.
FREE = "Matched an Alpha already simulated; no quota spent."

#: One scheduling pass at a time, so two passes cannot hand out the same free cores.
_scheduling = asyncio.Lock()


def cores_of(row: Study) -> int:
    return int((row.sampler_params or {}).get("cores") or 1)


def to_start(waiting: list[tuple[int, int]], used: int, slots: int) -> list[int]:
    """Waiting ``(task, cores)`` pairs, oldest first, that fit in the cores left.

    A task too big for what is free does not hold back a smaller one queued after it.
    """
    started: list[int] = []
    for task_id, cores in waiting:
        if used + cores <= slots:
            started.append(task_id)
            used += cores
    return started


def to_ask(batch_size: int, in_flight: int, left: int) -> int:
    """Simulations to queue now: the room the task's cores have, in tens so batches stay full."""
    room = batch_size - in_flight
    return max(0, min(room - room % 10, left))


async def start_waiting(optimizer: Optimizer) -> int:
    """Start queued tasks while their cores fit in the engine's slots. Returns how many."""
    async with _scheduling:
        async with optimizer.db.session() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Study).where(
                            Study.sampler.in_(TASK_SAMPLERS),
                            Study.status.in_([StudyStatus.RUNNING, StudyStatus.QUEUED]),
                        )
                    )
                ).all()
            )
        used = sum(cores_of(r) for r in rows if r.status == StudyStatus.RUNNING)
        waiting = sorted(
            (r for r in rows if r.status == StudyStatus.QUEUED),
            key=lambda r: (str((r.sampler_params or {}).get("queuedAt") or ""), r.id),
        )
        by_id = {r.id: r for r in waiting}
        started = to_start([(r.id, cores_of(r)) for r in waiting], used, optimizer.engine.slots)
        for task_id in started:
            await optimizer.engine.set_quota(by_id[task_id].task, cores_of(by_id[task_id]))
            await optimizer.set_status(task_id, StudyStatus.RUNNING)
    if started:
        log.info("tasks.started", tasks=started)
    return len(started)


def ask_points(
    study: optuna.Study,
    lab: Any,
    run: dict[str, Any],
    want: int,
    seen: dict[tuple[str, str], float | bool],
    cover: list[str],
) -> list[tuple[Any, dict[str, Any], SimulationRequest]]:
    """Ask for ``want`` new simulations. Runs off the event loop.

    A point this task already scored is answered with its score, one that failed is told as
    failed, and a repeat, or a point its lab cannot write, is pruned; each time another
    point is asked, so quota is only spent on Alphas the task has not seen.
    """
    from optuna.trial import TrialState as OptunaState

    space = run["space"]
    if cover and not study.get_trials(deepcopy=False, states=(OptunaState.WAITING,)):
        for field_id in cover:
            study.enqueue_trial(search.first_pass(space, field_id))

    choices = search.field_choices(space)
    picked: list[tuple[Any, dict[str, Any], SimulationRequest]] = []
    keys: set[tuple[str, str]] = set()
    for _ in range(want * 5):
        if len(picked) >= want:
            break
        trial = study.ask()
        params, request = lab.draw(trial, run, choices)
        key = None if request is None else search.identity(request)
        if key is None or key in keys or key in seen:
            known = None if key is None else seen.get(key)
            if known is False:
                study.tell(trial, state=OptunaState.FAIL)
            elif isinstance(known, float):
                study.tell(trial, known)
            else:
                study.tell(trial, state=OptunaState.PRUNED)
            continue
        keys.add(key)
        picked.append((trial, params, request))
    return picked


async def advance(optimizer: Optimizer, study_id: int) -> int:
    """Refill the task's cores as its batches come back. Returns how many were queued.

    A task keeps ten simulations in flight per core. Room freed by a returning batch is
    filled at once, in tens so every batch stays full, rather than once the slowest batch
    of a round is back.
    """
    async with optimizer.db.session() as session:
        row = await session.get(Study, study_id)
        if row is None or row.status != StudyStatus.RUNNING:
            return 0
        trials = list(
            (await session.scalars(select(Trial).where(Trial.study_id == study_id))).all()
        )
        in_flight = await session.scalar(
            select(func.count())
            .select_from(SimulationRecord)
            .where(
                SimulationRecord.task == row.task,
                SimulationRecord.is_batch.is_(False),
                SimulationRecord.status.in_(
                    [SimStatus.QUEUED, SimStatus.PENDING, SimStatus.RUNNING]
                ),
            )
        )

    run = row.sampler_params or {}
    waiting = any(t.state in (TrialState.QUEUED, TrialState.RUNNING) for t in trials)
    # Every trial not pruned or answered for free has spent, or is spending, a simulation.
    committed = sum(1 for t in trials if t.state != TrialState.PRUNED and t.message != FREE)
    if run.get("stopping") or committed >= row.max_trials:
        if not waiting:
            await optimizer.set_status(study_id, StudyStatus.COMPLETE)
        return 0
    want = to_ask(row.batch_size, int(in_flight or 0), row.max_trials - committed)
    if row.sampler == GA_SAMPLER:
        return await _breed(optimizer, row, trials, want, waiting)
    if row.sampler == POWER_POOL_SAMPLER:
        from . import power_pool  # imported here: labs.power_pool builds on this module

        return await power_pool.refill(optimizer, row, trials, want, waiting)
    if want <= 0:
        return 0

    if row.sampler == TEMPLATE_SAMPLER:
        from . import template as lab  # imported here: labs.template builds on labs.search
    else:
        lab = search

    space = run["space"]
    counted = [t for t in trials if t.state != TrialState.PRUNED]
    tried = {(t.params or {}).get("field") for t in counted}
    limit = row.max_trials // 2
    cover: list[str] = []
    if len(tried) < limit:
        untried = [f for f in space["fields"] if f not in tried]
        cover = untried[: min(want, limit - len(tried))]

    seen: dict[tuple[str, str], float | bool] = {}
    for t in counted:
        key = search.identity_of(t.expression, t.settings)
        if t.state == TrialState.COMPLETE and t.values:
            seen[key] = float(t.values[0])
        elif t.state == TrialState.FAIL:
            seen[key] = False

    study = await optimizer._optuna(study_id, row)
    picked = await asyncio.to_thread(ask_points, study, lab, run, want, seen, cover)
    if not picked:
        # Nothing new left to ask: the task is done once what is out has come back.
        if not waiting:
            await optimizer.set_status(study_id, StudyStatus.COMPLETE)
        return 0

    return await _queue(optimizer, row, trials, picked)


async def _queue(
    optimizer: Optimizer,
    row: Study,
    trials: list[Trial],
    picked: list[tuple[Any, dict[str, Any], SimulationRequest]],
) -> int:
    """Send new points to the engine and record them as trials.

    A point asked of the search carries its live trial, told when its simulation returns; a
    bred child carries none.
    """
    from optuna.distributions import distribution_to_json

    result = await optimizer.engine.enqueue(
        [request for _, _, request in picked], task=row.task, skip_duplicates=True
    )
    outcomes = result.get("outcomes", [])
    live = optimizer._open.setdefault(row.id, {})
    number = max((t.number for t in trials), default=-1)
    async with optimizer.db.session() as session:
        for index, (asked, params, request) in enumerate(picked):
            number += 1
            outcome = outcomes[index] if index < len(outcomes) else {}
            session.add(
                Trial(
                    study_id=row.id,
                    number=number,
                    params=params,
                    distributions={
                        name: distribution_to_json(distribution)
                        for name, distribution in (asked.distributions.items() if asked else ())
                    },
                    expression=request.regular,
                    settings=request.settings.model_dump(by_alias=True, exclude_none=True),
                    state=TrialState.QUEUED,
                    simulation_record_id=outcome.get("recordId"),
                    alpha_id=outcome.get("alphaId"),
                    message=FREE if outcome.get("status") == str(SimStatus.SKIPPED) else None,
                    generation=params.get("generation"),
                )
            )
            if asked is not None:
                live[number] = asked
        await session.commit()

    log.info("tasks.asked", study_id=row.id, trials=len(picked), task=row.task)
    return len(picked)


async def _breed(
    optimizer: Optimizer, row: Study, trials: list[Trial], want: int, waiting: bool
) -> int:
    """Evolution Lab's refill: children of the best Alphas so far, until the search stalls."""
    spent = [
        float(t.values[0]) if t.state == TrialState.COMPLETE and t.values else None
        for t in sorted(trials, key=lambda t: t.number)
        if (t.generation or 0) > 0
        and t.message != FREE
        and t.state in (TrialState.COMPLETE, TrialState.FAIL)
    ]
    if ga.stalled(spent):
        if not waiting:
            await _finish(optimizer, row.id, StudyStatus.COMPLETE, ga.STALLED)
        return 0
    if want <= 0:
        return 0
    picked = await ga.breed(optimizer, row, trials, want)
    if not picked:
        if not waiting:
            bred = any((t.generation or 0) > 0 for t in trials)
            await _finish(
                optimizer,
                row.id,
                StudyStatus.COMPLETE if bred else StudyStatus.FAILED,
                "No new child could be bred: every child of these parents was already "
                "simulated or is not a valid Alpha here.",
            )
        return 0
    return await _queue(optimizer, row, trials, [(None, params, req) for params, req in picked])


async def _finish(optimizer: Optimizer, study_id: int, status: StudyStatus, message: str) -> None:
    async with optimizer.db.session() as session:
        stored = await session.get(Study, study_id)
        if stored is not None:
            stored.status = status
            stored.message = message
            stored.finished_at = utcnow()
    await optimizer._notify()


async def prune_unsent(optimizer: Optimizer, study_id: int) -> int:
    """Mark trials whose simulation was taken off the queue as never run.

    They spent nothing, so they are pruned rather than failed: failing them would teach the
    search that these points score badly.
    """
    from optuna.trial import TrialState as OptunaState

    live = optimizer._open.get(study_id, {})
    study = optimizer._studies.get(study_id)
    pruned = 0
    async with optimizer.db.session() as session:
        open_trials = list(
            (
                await session.scalars(
                    select(Trial).where(
                        Trial.study_id == study_id,
                        Trial.state.in_([TrialState.QUEUED, TrialState.RUNNING]),
                    )
                )
            ).all()
        )
        ids = [t.simulation_record_id for t in open_trials if t.simulation_record_id]
        cancelled = set(
            (
                await session.scalars(
                    select(SimulationRecord.id).where(
                        SimulationRecord.id.in_(ids),
                        SimulationRecord.status == SimStatus.CANCELLED,
                    )
                )
            ).all()
        )
        for trial in open_trials:
            if trial.simulation_record_id and trial.simulation_record_id not in cancelled:
                continue
            trial.state = TrialState.PRUNED
            trial.message = "Taken off the queue before it was sent."
            trial.finished_at = utcnow()
            pruned += 1
            optuna_trial = live.pop(trial.number, None)
            if study is not None and optuna_trial is not None:
                study.tell(optuna_trial, state=OptunaState.PRUNED, skip_if_finished=True)
        await session.commit()
    return pruned
