"""Optuna, driven in rounds that fill whole batches.

The loop is deliberately not Optuna's ``study.optimize``. That call owns the thread and
evaluates one trial at a time; here a trial takes minutes on someone else's machine and
eighty of them run at once. So the study is driven by ask/tell:

    ask N points  ->  realise N simulations  ->  enqueue  ->  ... wait ...
    -> harvest finished alphas -> tell -> ask the next N

**N is a multiple of ten.** A multi-simulation carries ten children and there are eight
concurrent slots, so a round of eighty fills the platform exactly. Asking for 47 leaves
part-empty batches occupying whole slots for the length of their run.

Two consequences of batching are handled rather than ignored:

* Points asked for together cannot learn from one another. :mod:`.samplers` compensates
  where it can (``constant_liar`` for TPE) and says so where it cannot.
* Batch-splitting settings — region, delay, instrument type, language — are pinned by
  default instead of searched. Letting the sampler vary region across a round of eighty
  produces eighty batch keys, which turns eighty concurrent simulations into eight. It
  is still allowed, but only on purpose.

Restart safety: the Optuna study is rebuilt from the trial rows, never persisted
separately. Trials live in the application's own database so they stay joinable to the
simulations that produced them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import func, select

from ..brain.endpoints import BrainEndpoints
from ..db.models import (
    SimStatus,
    SimulationRecord,
    Study,
    StudyStatus,
    Trial,
    TrialState,
    utcnow,
)
from ..db.sqlite import Database
from ..engine.slots import BatchEngine
from ..templates.expand import realise
from ..templates.resolver import Prepared
from ..templates.schema import (
    BATCH_SPLITTING_SETTINGS,
    ChoiceVar,
    DataFieldVar,
    FloatVar,
    IntVar,
    TemplateSpec,
    parse,
    setting_options,
)
from ..templates.studio import Studio
from . import objectives as obj
from . import samplers as samp
from .errors import StudyError, StudyNotFoundError

if TYPE_CHECKING:  # pragma: no cover
    import optuna

    from ..vault.backfill import Backfill
    from ..vault.store import AlphaVault

log = structlog.get_logger(__name__)

#: Prefix distinguishing a swept setting from a template variable in the parameter
#: space. Chosen so it cannot collide with a YAML variable name.
SETTING_PREFIX = "setting:"

#: How often a running study looks for finished trials.
POLL_SECONDS = 5.0

#: Studies with this sampler breed generations (labs.ga) instead of asking Optuna.
GA_SAMPLER = "ga"
#: Studies with this sampler write their own expressions (labs.search), asked define-by-run.
SEARCH_SAMPLER = "search"
TEMPLATE_SAMPLER = "template"
POWER_POOL_SAMPLER = "power-pool"
#: Studies that are research-lab tasks, run only from the Tasks tab, by their lab's name.
TASK_SAMPLERS = {
    SEARCH_SAMPLER: "Search Lab",
    TEMPLATE_SAMPLER: "Template Lab",
    GA_SAMPLER: "Evolution Lab",
    POWER_POOL_SAMPLER: "LLM Power Pool Lab",
}
#: Statistics the local Alpha store keeps, so objectives on them need no second BRAIN read.
VAULT_STATS = frozenset(
    {"sharpe", "fitness", "turnover", "returns", "drawdown", "margin"}
    | {"train_sharpe", "train_fitness"}
)
#: How long a finished Alpha may take to reach the local store before BRAIN is asked.
VAULT_WAIT = timedelta(minutes=5)


__all__ = [
    "Optimizer",
    "StudyError",
    "StudyNotFoundError",
    "build_space",
    "pareto_front",
    "split_params",
]


def serialise_trial(row: Trial) -> dict[str, Any]:
    return {
        "id": row.id,
        "number": row.number,
        "state": row.state,
        "params": row.params,
        "expression": row.expression,
        "settings": row.settings,
        "values": row.values,
        "constraint": row.constraint,
        "feasible": row.feasible,
        "result": row.result,
        "message": row.message,
        "simulationRecordId": row.simulation_record_id,
        "alphaId": row.alpha_id,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
    }


def serialise_study(row: Study, counts: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "templateId": row.template_id,
        "templateName": row.template_name,
        "sampler": row.sampler,
        "samplerParams": row.sampler_params,
        "samplerNotes": list(row.sampler_notes or []),
        "objectives": list(row.objectives or []),
        "directions": list(row.directions or []),
        "batchSize": row.batch_size,
        "maxTrials": row.max_trials,
        "task": row.task,
        "seed": row.seed,
        "status": row.status,
        "message": row.message,
        "trials": counts or {},
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
    }


class Optimizer:
    """Owns every running study."""

    def __init__(
        self,
        db: Database,
        engine: BatchEngine,
        endpoints: BrainEndpoints,
        studio: Studio,
        *,
        on_change: Any = None,
        alphas: AlphaVault | None = None,
        backfill: Backfill | None = None,
    ) -> None:
        self.db = db
        self.engine = engine
        self.endpoints = endpoints
        self.studio = studio
        self._on_change = on_change
        #: Needed only by objectives scored from daily PnL (K-Ratio).
        self.alphas = alphas
        self.backfill = backfill
        self._studies: dict[int, optuna.Study] = {}
        #: Live Optuna trial objects for the round in flight, keyed study -> our trial
        #: number. Lost on restart, which :func:`_tell_many` handles by replaying.
        self._open: dict[int, dict[int, Any]] = {}
        self._prepared: dict[int, Prepared] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="optimizer")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        log.info("optimize.stopped")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("optimize.tick_failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=POLL_SECONDS)
            except TimeoutError:
                continue

    async def tick(self) -> dict[int, dict[str, int]]:
        """Start waiting tasks that fit, then advance every running study by one round."""
        from ..labs import tasks  # imported here: labs.tasks builds on this module

        try:
            await tasks.start_waiting(self)
        except Exception:
            log.exception("optimize.schedule_failed")
        async with self.db.session() as session:
            ids = list(
                (
                    await session.scalars(
                        select(Study.id).where(Study.status == StudyStatus.RUNNING)
                    )
                ).all()
            )
        results: dict[int, dict[str, int]] = {}
        for study_id in ids:
            try:
                results[study_id] = await self.advance(study_id)
            except Exception as exc:
                log.exception("optimize.study_failed", study_id=study_id)
                await self._fail(study_id, str(exc))
        return results

    def _lock(self, study_id: int) -> asyncio.Lock:
        return self._locks.setdefault(study_id, asyncio.Lock())

    # -- creating --------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        template_source: str,
        template_id: int | None = None,
        sampler: str = samp.DEFAULT_SAMPLER,
        sampler_params: dict[str, Any] | None = None,
        objective_keys: list[str] | None = None,
        batch_size: int = samp.BATCH_SIZE,
        max_trials: int = 80,
        seed: int | None = None,
        task: str | None = None,
        search_batch_settings: bool = False,
    ) -> Study:
        """Set a study up, without starting it.

        Everything that can be wrong is caught here — an unparseable template, an
        impossible sampler/objective pairing, a search space with nothing in it, a
        variable that resolves to no fields — so a study that reaches ``RUNNING`` is one
        that can actually run.

        The template is checked with the *same* review the editor shows, rather than a
        weaker check of its own. A study is the one thing here that spends quota
        unattended; it must not accept a template the Template Studio would have
        rejected.
        """
        spec = parse(template_source)
        keys = objective_keys or list(obj.DEFAULT_OBJECTIVES)
        resolved = obj.resolve(keys)

        review = await self.studio.review(spec, expand_grid=False)
        if not review.report.ok:
            raise StudyError(
                "This template cannot run as written. "
                + " ".join(p.message for p in review.report.errors[:3])
            )

        prepared = review.prepared
        space = build_space(spec, prepared, search_batch_settings=search_batch_settings)
        if not space:
            raise StudyError(
                "This template has nothing to search: every variable and setting has a "
                "single value. Give a variable a grid or a range, or run it as a sweep "
                "from the Template Studio instead."
            )

        # A round of seven asks for one multi-simulation with three empty children,
        # holding a whole concurrent slot to run seven simulations instead of ten. One
        # is the exception: it means "do not batch", which is what an account without
        # MULTI_SIMULATION gets anyway.
        batch = 1 if batch_size <= 1 else samp.round_population(batch_size, samp.BATCH_SIZE)
        notes: list[str] = []
        if batch != batch_size:
            notes.append(
                f"Batch size {batch_size} → {batch}. A multi-simulation carries "
                f"{samp.BATCH_SIZE} children, so anything else leaves slots holding "
                "part-empty batches for the length of their run."
            )

        _, sampler_notes = samp.build(
            sampler,
            batch_size=batch,
            n_objectives=len(resolved),
            seed=seed,
            params=sampler_params,
            search_space={k: v.get("choices", []) for k, v in space.items()},
        )
        notes.extend(sampler_notes)

        pinned = [n for n in spec.batch_splitting_sweeps if f"{SETTING_PREFIX}{n}" not in space]
        if pinned:
            notes.append(
                "Pinned "
                + " and ".join(pinned)
                + " to their first value. Searching them would give almost every trial "
                "its own batch key, turning eighty concurrent simulations into eight."
            )

        row = Study(
            name=name,
            template_id=template_id,
            template_name=spec.name,
            template_source=template_source,
            sampler=sampler,
            sampler_params=sampler_params or {},
            sampler_notes=notes,
            objectives=keys,
            directions=[o.direction for o in resolved],
            seed=seed,
            batch_size=batch,
            max_trials=max(batch, max_trials),
            task=task or f"study:{name}"[:64],
            status=StudyStatus.IDLE,
        )
        async with self.db.session() as session:
            clash = await session.scalar(select(Study).where(Study.name == name))
            if clash is not None:
                raise StudyError(f"A study named {name!r} already exists.")
            session.add(row)
            await session.commit()
            await session.refresh(row)

        log.info("optimize.created", study=name, sampler=sampler, batch=batch, notes=notes)
        return row

    # -- control ---------------------------------------------------------

    async def set_status(self, study_id: int, status: StudyStatus) -> Study:
        async with self.db.session() as session:
            row = await session.get(Study, study_id)
            if row is None:
                raise StudyNotFoundError(study_id)
            row.status = status
            if status in (StudyStatus.COMPLETE, StudyStatus.FAILED):
                row.finished_at = utcnow()
            await session.commit()
            await session.refresh(row)
        await self._notify()
        return row

    async def _fail(self, study_id: int, message: str) -> None:
        async with self.db.session() as session:
            row = await session.get(Study, study_id)
            if row is not None:
                row.status = StudyStatus.FAILED
                row.message = message
                row.finished_at = utcnow()
                await session.commit()
        await self._notify()

    # -- the round -------------------------------------------------------

    async def advance(self, study_id: int) -> dict[str, int]:
        """Harvest what finished, then ask for the next round if there is room."""
        async with self._lock(study_id):
            row = await self.get(study_id)
            if row is not None and row.sampler in TASK_SAMPLERS:
                from ..labs import tasks  # imported here: labs.tasks builds on this module

                # Evolution Lab breeds its own children: there is no sampler to tell.
                told = await self._harvest(
                    study_id,
                    vault_first=True,
                    tell=row.sampler in (SEARCH_SAMPLER, TEMPLATE_SAMPLER),
                )
                asked = await tasks.advance(self, study_id)
            else:
                told = await self._harvest(study_id)
                asked = await self._ask_round(study_id)
        if told or asked:
            await self._notify()
        return {"told": told, "asked": asked}

    async def _ask_round(self, study_id: int) -> int:
        async with self.db.session() as session:
            row = await session.get(Study, study_id)
            if row is None or row.status != StudyStatus.RUNNING:
                return 0
            counts = await self._counts(session, study_id)

        done = counts.get("COMPLETE", 0) + counts.get("FAIL", 0) + counts.get("PRUNED", 0)
        open_trials = counts.get("QUEUED", 0) + counts.get("RUNNING", 0)
        total = done + open_trials

        if done >= row.max_trials:
            await self.set_status(study_id, StudyStatus.COMPLETE)
            return 0
        # One round in flight at a time. A second round asked before the first returns
        # would be sampled from the same unchanged model — the exact thing constant_liar
        # only partly mitigates.
        if open_trials:
            return 0

        want = min(row.batch_size, row.max_trials - total)
        if total == 0:
            # The exploration wave. TPE's startup trials are random picks that learn from
            # nothing, so asking for all of them at once costs no search quality and lets
            # every core start immediately instead of eighty at a time.
            startup = int((row.sampler_params or {}).get("n_startup_trials") or 0)
            wave = -(-startup // row.batch_size) * row.batch_size
            want = min(max(row.batch_size, wave), row.max_trials)
        if want <= 0:
            return 0

        spec = parse(row.template_source)
        prepared = await self._prepare(study_id, spec)
        space = build_space(spec, prepared, search_batch_settings=self._searches_settings(row))

        study = await self._optuna(study_id, row)
        asked = await asyncio.to_thread(_ask_many, study, space, want)

        requests = []
        for _optuna_trial, params in asked:
            assignment, settings_choice = split_params(spec, params, prepared)
            requests.append(realise(spec, assignment, settings_choice))

        result = await self.engine.enqueue(requests, task=row.task, skip_duplicates=True)
        outcomes = result.get("outcomes", [])

        open_trials = self._open.setdefault(study_id, {})
        async with self.db.session() as session:
            next_number = int(
                await session.scalar(
                    select(func.coalesce(func.max(Trial.number), -1)).where(
                        Trial.study_id == study_id
                    )
                )
                or -1
            )
            for index, (optuna_trial, params) in enumerate(asked):
                next_number += 1
                outcome = outcomes[index] if index < len(outcomes) else {}
                request = requests[index]
                skipped = outcome.get("status") == str(SimStatus.SKIPPED)
                session.add(
                    Trial(
                        study_id=study_id,
                        number=next_number,
                        params=_jsonable(params),
                        distributions=_dump_distributions(space, params),
                        expression=request.regular,
                        settings=request.settings.model_dump(by_alias=True, exclude_none=True),
                        state=TrialState.QUEUED,
                        simulation_record_id=outcome.get("recordId"),
                        alpha_id=outcome.get("alphaId"),
                        message=(
                            "Matched an alpha already simulated; scored without spending any quota."
                            if skipped
                            else None
                        ),
                    )
                )
                open_trials[next_number] = optuna_trial
            await session.commit()

        log.info("optimize.asked", study_id=study_id, trials=len(asked), task=row.task)
        return len(asked)

    async def _harvest(self, study_id: int, *, tell: bool = True, vault_first: bool = False) -> int:
        """Score every trial whose simulation reached a terminal state.

        ``tell=False`` stores the scores without reporting them to Optuna, for GA studies.
        ``vault_first`` reads the Alpha the tracker already stored locally instead of asking
        BRAIN for it again, which saves one request per simulation.
        """
        async with self.db.session() as session:
            row = await session.get(Study, study_id)
            if row is None:
                return 0
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
            records = {
                r.id: r
                for r in (
                    await session.scalars(
                        select(SimulationRecord).where(
                            SimulationRecord.id.in_(
                                [
                                    t.simulation_record_id
                                    for t in open_trials
                                    if t.simulation_record_id
                                ]
                            )
                        )
                    )
                ).all()
            }

        objective_list = obj.resolve(list(row.objectives or []))
        saved: dict[str, dict[str, Any]] = {}
        if (
            vault_first
            and self.alphas is not None
            and all(o.key in VAULT_STATS for o in objective_list)
        ):
            wanted = [
                (
                    records[t.simulation_record_id].alpha_id
                    if t.simulation_record_id in records
                    else None
                )
                or t.alpha_id
                for t in open_trials
            ]
            saved = await self.alphas.by_ids([a for a in wanted if a])
        #: (live optuna trial | None, stored row, values | None, summary)
        finished: list[tuple[Any, Trial, list[float] | None, dict[str, Any]]] = []
        started: list[int] = []
        live = self._open.get(study_id, {})

        for trial in open_trials:
            record = records.get(trial.simulation_record_id or -1)
            if record is None:
                finished.append(
                    (
                        live.get(trial.number),
                        trial,
                        None,
                        {"error": "The simulation row for this trial is gone."},
                    )
                )
                continue

            status = SimStatus(record.status)
            if not status.terminal:
                if status == SimStatus.RUNNING and trial.state != TrialState.RUNNING:
                    started.append(trial.id)
                continue

            alpha_id = record.alpha_id or trial.alpha_id
            if not alpha_id:
                finished.append(
                    (
                        live.get(trial.number),
                        trial,
                        None,
                        {
                            "error": record.message
                            or f"The simulation ended {record.status} with no alpha."
                        },
                    )
                )
                continue

            if vault_first:
                stored_alpha = saved.get(alpha_id)
                if stored_alpha is not None and stored_alpha.get("sharpe") is not None:
                    values = [
                        o.failure_value
                        if stored_alpha.get(o.key) is None
                        else float(stored_alpha[o.key])
                        for o in objective_list
                    ]
                    summary = _vault_summary(stored_alpha)
                    finished.append((live.get(trial.number), trial, values, summary))
                    continue
                if record.finished_at is not None:
                    ended = record.finished_at
                    ended = ended if ended.tzinfo else ended.replace(tzinfo=UTC)
                    now = utcnow()
                    now = now if now.tzinfo else now.replace(tzinfo=UTC)
                    if now - ended < VAULT_WAIT:
                        continue  # The tracker stores it moments after it finishes.

            try:
                alpha = await self.endpoints.get_alpha(alpha_id)
            except Exception as exc:
                finished.append(
                    (
                        live.get(trial.number),
                        trial,
                        None,
                        {"error": f"Could not read the alpha back: {exc}"},
                    )
                )
                continue

            extra = obj.train_extra(alpha, objective_list)
            if any(o.needs_pnl for o in objective_list):
                extra["k_ratio"] = await self._k_ratio(alpha_id)
            values = obj.extract(alpha.in_sample, objective_list, extra)
            summary = obj.summarise(alpha) | (
                {"kRatio": extra["k_ratio"]} if "k_ratio" in extra else {}
            )
            finished.append((live.get(trial.number), trial, values, summary))

        if started:
            await self._mark_running(started)
        if not finished:
            return 0

        if tell:
            study = await self._optuna(study_id, row)
            await asyncio.to_thread(_tell_many, study, finished)

        async with self.db.session() as session:
            for _optuna_trial, trial, values, summary in finished:
                live.pop(trial.number, None)
                stored = await session.get(Trial, trial.id)
                if stored is None:
                    continue
                stored.finished_at = utcnow()
                if values is None:
                    stored.state = TrialState.FAIL
                    stored.message = summary.get("error")
                else:
                    stored.state = TrialState.COMPLETE
                    stored.values = values
                    stored.constraint = summary.get("constraint")
                    stored.feasible = summary.get("feasible")
                    stored.result = summary
                    stored.alpha_id = summary.get("alphaId")
            await session.commit()

        log.info("optimize.told", study_id=study_id, trials=len(finished))
        return len(finished)

    async def _mark_running(self, trial_ids: list[int]) -> None:
        async with self.db.session() as session:
            for trial_id in trial_ids:
                stored = await session.get(Trial, trial_id)
                if stored is not None:
                    stored.state = TrialState.RUNNING
            await session.commit()

    async def _k_ratio(self, alpha_id: str) -> float | None:
        """Fetch the daily series if missing, then score it.

        ponytail: sequential, one Retry-After request per trial inside the harvest; run
        them concurrently if K-Ratio rounds visibly lag.
        """
        if self.alphas is None or self.backfill is None:
            return None
        try:
            if await self.alphas.series_length(alpha_id) == 0:
                await self.backfill.fetch_returns(alpha_id)
            return await self.alphas.k_ratio(alpha_id)
        except Exception:
            log.warning("optimize.k_ratio_failed", alpha_id=alpha_id, exc_info=True)
            return None

    # -- optuna ----------------------------------------------------------

    async def _optuna(self, study_id: int, row: Study) -> optuna.Study:
        """The Optuna study, rebuilt from the trial rows on first use."""
        cached = self._studies.get(study_id)
        if cached is not None:
            return cached

        async with self.db.session() as session:
            trials = list(
                (
                    await session.scalars(
                        select(Trial).where(Trial.study_id == study_id).order_by(Trial.number)
                    )
                ).all()
            )
            history = [
                {
                    "params": t.params or {},
                    "distributions": t.distributions or {},
                    "values": t.values,
                    "state": t.state,
                    "constraint": t.constraint or {},
                }
                for t in trials
            ]

        sampler, _ = samp.build(
            "tpe" if row.sampler in TASK_SAMPLERS else row.sampler,
            batch_size=row.batch_size,
            n_objectives=len(row.objectives or []),
            seed=row.seed,
            params=row.sampler_params or {},
        )
        study = await asyncio.to_thread(_rebuild, sampler, list(row.directions or []), history)
        self._studies[study_id] = study
        return study

    async def _prepare(self, study_id: int, spec: TemplateSpec) -> Prepared:
        cached = self._prepared.get(study_id)
        if cached is None:
            cached = await self.studio.resolver.prepare(spec)
            self._prepared[study_id] = cached
        return cached

    def _searches_settings(self, row: Study) -> bool:
        return bool((row.sampler_params or {}).get("search_batch_settings"))

    def forget(self, study_id: int) -> None:
        """Drop cached state — after deletion, or when a study is reset."""
        self._studies.pop(study_id, None)
        self._prepared.pop(study_id, None)
        self._locks.pop(study_id, None)
        self._open.pop(study_id, None)

    # -- reading ---------------------------------------------------------

    async def _counts(self, session: Any, study_id: int) -> dict[str, int]:
        rows = await session.execute(
            select(Trial.state, func.count())
            .where(Trial.study_id == study_id)
            .group_by(Trial.state)
        )
        return {str(state): int(n) for state, n in rows.all()}

    async def counts(self, study_id: int) -> dict[str, int]:
        async with self.db.session() as session:
            return await self._counts(session, study_id)

    async def get(self, study_id: int) -> Study | None:
        async with self.db.session() as session:
            return await session.get(Study, study_id)

    async def list_all(self) -> list[tuple[Study, dict[str, int]]]:
        async with self.db.session() as session:
            rows = list((await session.scalars(select(Study).order_by(Study.id.desc()))).all())
            return [(r, await self._counts(session, r.id)) for r in rows]

    async def trials(self, study_id: int, *, limit: int = 500) -> list[Trial]:
        async with self.db.session() as session:
            return list(
                (
                    await session.scalars(
                        select(Trial)
                        .where(Trial.study_id == study_id)
                        .order_by(Trial.number)
                        .limit(limit)
                    )
                ).all()
            )

    async def delete(self, study_id: int) -> None:
        async with self.db.session() as session:
            row = await session.get(Study, study_id)
            if row is None:
                raise StudyNotFoundError(study_id)
            await session.delete(row)
            await session.commit()
        self.forget(study_id)

    async def pareto(self, study_id: int) -> dict[str, Any]:
        """The non-dominated trials — the actual answer a multi-objective study gives.

        There is no single best alpha when optimising Sharpe against turnover; there is a
        frontier, and choosing a point on it is a judgement about what you want to run.
        Infeasible trials are excluded, because an alpha that fails a submission check is
        not on anyone's frontier.
        """
        row = await self.get(study_id)
        if row is None:
            raise StudyNotFoundError(study_id)

        trials = [
            t
            for t in await self.trials(study_id, limit=10_000)
            if t.state == TrialState.COMPLETE and t.values
        ]
        feasible = [t for t in trials if t.feasible is not False]
        directions = list(row.directions or [])

        front = pareto_front(feasible, directions)
        deduped, repeats = _distinct_points(front)
        return {
            "objectives": list(row.objectives or []),
            "directions": directions,
            "complete": len(trials),
            "feasible": len(feasible),
            "front": [serialise_trial(t) for t in deduped],
            # A sampler revisiting a point is normal and free — the second visit is
            # answered from the dedup cache. But the frontier is a set of choices, and
            # the same alpha listed twice reads as two of them.
            "frontRepeats": repeats,
            "all": [serialise_trial(t) for t in trials],
        }

    async def _notify(self) -> None:
        if self._on_change is None:
            return
        result = self._on_change({"kind": "studies"})
        if asyncio.iscoroutine(result):
            await result


# --- the search space -----------------------------------------------------


def build_space(
    spec: TemplateSpec,
    prepared: Prepared | None = None,
    *,
    search_batch_settings: bool = False,
) -> dict[str, dict[str, Any]]:
    """Template variables and swept settings as Optuna distributions.

    Everything is described declaratively rather than by calling ``trial.suggest_*``
    directly, so the same description can be shown to the user, replayed when a study is
    rebuilt, and used to detect that a template no longer matches its study.
    """
    space: dict[str, dict[str, Any]] = {}

    for name, variable in spec.vars.items():
        if name in spec.unused_vars:
            continue
        if isinstance(variable, DataFieldVar):
            values = list(variable.values or [])
            if not values and prepared is not None:
                values = prepared(variable)
            if len(values) > 1:
                space[name] = {"kind": "categorical", "choices": values}
        elif isinstance(variable, ChoiceVar):
            if len(variable.values) > 1:
                space[name] = {"kind": "categorical", "choices": list(variable.values)}
        elif isinstance(variable, IntVar):
            if variable.grid is not None:
                if len(variable.grid) > 1:
                    space[name] = {"kind": "categorical", "choices": list(variable.grid)}
            elif (
                variable.low is not None
                and variable.high is not None
                and variable.low < variable.high
            ):
                space[name] = {
                    "kind": "int",
                    "low": variable.low,
                    "high": variable.high,
                    "step": variable.step,
                    "log": variable.log,
                }
        elif isinstance(variable, FloatVar):
            if variable.grid is not None:
                if len(variable.grid) > 1:
                    space[name] = {"kind": "categorical", "choices": list(variable.grid)}
            elif (
                variable.low is not None
                and variable.high is not None
                and variable.low < variable.high
            ):
                space[name] = {
                    "kind": "float",
                    "low": variable.low,
                    "high": variable.high,
                    "step": variable.step,
                    "log": variable.log,
                }

    for name, value in spec.settings.items():
        options = setting_options(value)
        if len(options) <= 1:
            continue
        if name in BATCH_SPLITTING_SETTINGS and not search_batch_settings:
            continue
        space[f"{SETTING_PREFIX}{name}"] = {"kind": "categorical", "choices": options}

    return space


def split_params(
    spec: TemplateSpec, params: dict[str, Any], prepared: Prepared | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate expression variables from settings, and fill in what was pinned.

    A setting that was pinned rather than searched still has to reach the request, so it
    is taken from the template's first value — which is what ``realise`` would do anyway,
    stated here so the trial record shows the settings actually used.
    """
    assignment: dict[str, Any] = {}
    settings_choice: dict[str, Any] = {}

    for key, value in params.items():
        if key.startswith(SETTING_PREFIX):
            settings_choice[key[len(SETTING_PREFIX) :]] = value
        else:
            assignment[key] = value

    for name, variable in spec.vars.items():
        if name in assignment:
            continue
        # Single-valued variables never enter the search space, but the expression still
        # needs them substituted.
        if (isinstance(variable, DataFieldVar) and variable.values) or isinstance(
            variable, ChoiceVar
        ):
            assignment[name] = variable.values[0]
        elif isinstance(variable, DataFieldVar) and prepared is not None:
            # A catalog filter that resolved to exactly one field is left out of the
            # space too; without this the placeholder survived and the study failed.
            resolved = prepared(variable)
            if resolved:
                assignment[name] = resolved[0]
        elif isinstance(variable, IntVar | FloatVar) and variable.grid:
            assignment[name] = variable.grid[0]
        elif isinstance(variable, IntVar | FloatVar) and variable.low is not None:
            assignment[name] = variable.low

    for name, value in spec.settings.items():
        if name not in settings_choice:
            settings_choice[name] = setting_options(value)[0]

    return assignment, settings_choice


# --- optuna plumbing, all synchronous ------------------------------------


def _distribution(spec: dict[str, Any]) -> Any:
    from optuna.distributions import (
        CategoricalDistribution,
        FloatDistribution,
        IntDistribution,
    )

    match spec["kind"]:
        case "categorical":
            return CategoricalDistribution(tuple(spec["choices"]))
        case "int":
            return IntDistribution(
                low=spec["low"],
                high=spec["high"],
                step=spec.get("step") or 1,
                log=spec.get("log", False),
            )
        case "float":
            return FloatDistribution(
                low=spec["low"],
                high=spec["high"],
                step=spec.get("step"),
                log=spec.get("log", False),
            )
    raise ValueError(f"Unknown distribution kind {spec['kind']!r}")


def _dump_distributions(space: dict[str, dict[str, Any]], params: dict[str, Any]) -> dict[str, str]:
    from optuna.distributions import distribution_to_json

    return {
        name: distribution_to_json(_distribution(spec))
        for name, spec in space.items()
        if name in params
    }


def _jsonable(params: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (v if isinstance(v, str | int | float | bool | type(None)) else str(v))
        for k, v in params.items()
    }


def _ask_many(
    study: optuna.Study, space: dict[str, dict[str, Any]], n: int
) -> list[tuple[Any, dict[str, Any]]]:
    """Ask for ``n`` points at once. Runs off the event loop.

    The live ``Trial`` objects are returned, not just their parameters. Constraints are
    attached to a trial before it is told, and doing that through the object is the
    public route; the alternative is reaching into Optuna's storage by trial number.
    """
    distributions = {name: _distribution(spec) for name, spec in space.items()}
    return [(t, dict(t.params)) for t in (study.ask(distributions) for _ in range(n))]


def _tell_many(
    study: optuna.Study,
    finished: list[tuple[Any, Any, list[float] | None, dict[str, Any]]],
) -> None:
    """Report results.

    Two routes, because a trial asked before a restart no longer has a live object:

    * **live** — set each constraint on the trial, then tell it. The ordinary path.
    * **replayed** — add it as an already-finished trial. Same information reaches the
      sampler, through entirely public API, without depending on Optuna's own numbering
      matching ours.

    Constraints are named rather than positional (Optuna 5's ``set_constraint``), so the
    keys are BRAIN's own check names and a check appearing later cannot silently change
    what an earlier position meant.
    """
    from optuna.trial import TrialState as OptunaState
    from optuna.trial import create_trial

    for optuna_trial, stored, values, summary in finished:
        constraint = {str(k): float(v) for k, v in (summary.get("constraint") or {}).items()}
        try:
            if optuna_trial is not None:
                if values is None:
                    study.tell(optuna_trial, state=OptunaState.FAIL, skip_if_finished=True)
                else:
                    for key, violation in constraint.items():
                        optuna_trial.set_constraint(key, violation)
                    study.tell(optuna_trial, values, skip_if_finished=True)
                continue

            if values is None:
                continue  # A failed replayed trial teaches the sampler nothing.
            distributions = _load_distributions(stored.distributions or {})
            study.add_trial(
                create_trial(
                    params={k: v for k, v in (stored.params or {}).items() if k in distributions},
                    distributions=distributions,
                    values=[float(v) for v in values],
                    state=OptunaState.COMPLETE,
                    constraints=constraint or None,
                )
            )
        except Exception:
            log.warning("optimize.tell_failed", number=stored.number, exc_info=True)


def _load_distributions(raw: dict[str, Any]) -> dict[str, Any]:
    from optuna.distributions import json_to_distribution

    return {name: json_to_distribution(value) for name, value in raw.items()}


def _rebuild(sampler: Any, directions: list[str], history: list[dict[str, Any]]) -> optuna.Study:
    """Recreate a study from its finished trials.

    Only terminal trials are replayed. Open ones are left out: their live objects did not
    survive, so they are told later as replayed trials, and recreating them would only
    reserve numbers we do not use — the application keeps its own.
    """
    import optuna
    from optuna.trial import TrialState as OptunaState
    from optuna.trial import create_trial

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(directions=directions or ["maximize"], sampler=sampler)

    state_map = {
        "COMPLETE": OptunaState.COMPLETE,
        "FAIL": OptunaState.FAIL,
        "PRUNED": OptunaState.PRUNED,
    }

    for entry in history:
        state = state_map.get(str(entry["state"]))
        if state is None:
            continue
        distributions = _load_distributions(entry["distributions"] or {})
        params = {k: v for k, v in (entry["params"] or {}).items() if k in distributions}
        values = (
            [float(v) for v in (entry["values"] or [])] if state is OptunaState.COMPLETE else None
        )
        if state is OptunaState.COMPLETE and not values:
            continue
        constraints = {str(k): float(v) for k, v in (entry["constraint"] or {}).items()}
        study.add_trial(
            create_trial(
                params=params,
                distributions=distributions,
                values=values,
                state=state,
                constraints=constraints or None,
            )
        )
    return study


def ranked(trials: list[Trial], directions: list[str] | None) -> list[dict[str, Any]]:
    """Finished trials, best first on the first objective."""
    maximize = (list(directions or []) or ["maximize"])[0] == "maximize"
    done = [t for t in trials if t.state == TrialState.COMPLETE and t.values]
    done.sort(key=lambda t: float(t.values[0]), reverse=maximize)
    rows = []
    for t in done:
        result = t.result or {}
        stats = result.get("stats") or {}
        rows.append(
            {
                "trialId": t.id,
                "alphaId": t.alpha_id,
                "expression": t.expression,
                "settings": t.settings,
                "value": t.values[0],
                "sharpe": stats.get("sharpe"),
                "fitness": stats.get("fitness"),
                "turnover": stats.get("turnover"),
                "returns": stats.get("returns"),
                "drawdown": stats.get("drawdown"),
                "margin": stats.get("margin"),
                "kRatio": result.get("kRatio"),
                "feasible": t.feasible,
                "failedChecks": result.get("failedChecks") or [],
            }
        )
    return rows


def _vault_summary(saved: dict[str, Any]) -> dict[str, Any]:
    """The result row for an Alpha read from the local store instead of from BRAIN."""
    try:
        checks = json.loads(saved.get("checks") or "[]")
    except ValueError:
        checks = []
    checks = [c for c in checks if isinstance(c, dict)] if isinstance(checks, list) else []
    failed = [c.get("name") for c in checks if c.get("result") == "FAIL"]
    return {
        "alphaId": saved.get("alpha_id"),
        "grade": saved.get("grade"),
        "stats": {key: saved.get(key) for key in sorted(VAULT_STATS)},
        "checks": checks,
        "feasible": not failed if checks else None,
        "failedChecks": failed,
    }


def _distinct_points(trials: list[Any]) -> tuple[list[Any], int]:
    """Collapse trials that are the same point, keeping the earliest.

    A sampler revisiting a point costs nothing — the repeat is answered from the dedup
    cache — but the frontier is a menu of choices, and the same alpha appearing twice
    reads as two of them. Identity is the alpha where there is one, and the parameters
    otherwise, so a repeat is recognised even before its simulation lands.
    """
    seen: set[Any] = set()
    kept: list[Any] = []
    repeats = 0
    for trial in trials:
        key = trial.alpha_id or tuple(sorted((trial.params or {}).items()))
        if key in seen:
            repeats += 1
            continue
        seen.add(key)
        kept.append(trial)
    return kept, repeats


def pareto_front(trials: list[Any], directions: list[str]) -> list[Any]:
    """Non-dominated trials, computed from the stored rows.

    Optuna's own ``best_trials`` needs the live study object; this works from the
    database, so the frontier is available after a restart and without rebuilding
    anything. A trial dominates another when it is no worse on every objective and
    strictly better on at least one.
    """

    def better(a: float, b: float, direction: str) -> bool:
        return a > b if direction == "maximize" else a < b

    scored = [
        (t, [float(v) for v in (t.values or [])])
        for t in trials
        if t.values and len(t.values) == len(directions)
    ]

    front: list[Any] = []
    for index, (trial, values) in enumerate(scored):
        dominated = False
        for other_index, (_other, other) in enumerate(scored):
            if other_index == index:
                continue
            no_worse = all(
                not better(mine, theirs, d)
                for mine, theirs, d in zip(values, other, directions, strict=True)
            )
            strictly = any(
                better(theirs, mine, d)
                for mine, theirs, d in zip(values, other, directions, strict=True)
            )
            if no_worse and strictly:
                dominated = True
                break
        if not dominated:
            front.append(trial)
    return front
