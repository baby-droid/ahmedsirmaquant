"""Turning the PM's allocation into queued work.

The PM decides which desks get cores. This is what makes that decision real: it asks each
funded desk for its share of the day, queues the result under a task named for the lab,
and gives that task the cores it was allocated. After this returns, :class:`BatchEngine`
drains the queue across free slots on its own — nothing here runs a loop.

**Why the task name carries the lab and the day.** Slot quotas and progress are keyed per
task, but yield is judged per lab across many days, so ``sweep-2026-09-08-1`` has to be
readable both ways. :func:`~.yields.lab_of` reads the lab back off the front.

**Not every desk can be dispatched this way, and that is stated rather than hidden.**
Sweep, Pair, Invent, Repair, Relocate, Harden and Diversify all answer with a concrete
list of simulations, so they queue immediately. Tuning drives its own optimiser loop
across several rounds, and Combining works out what a mix would score before spending
anything at all, so both are reported as allocated-but-not-started rather than quietly
dropped — a briefing that claims four desks while one runs is worse than a briefing that
admits two.
"""

from __future__ import annotations

from typing import Any

import structlog

from ..brain.schemas import SimulationRequest, SimulationSettings
from ..catalog.queries import Tuple4
from ..db.models import StudyStatus
from ..labs import deepen
from .day import today

log = structlog.get_logger(__name__)

#: Every desk the Head of Research can fund. Tuning is here too, even though it is the
#: one that does not answer with a list of simulations — see :meth:`Dispatcher.run`.
DISPATCHABLE = frozenset(
    {"sweep", "pair", "invent", "deepen", "combine", "repair", "relocate", "harden", "diversify"}
)

#: Why a desk that was funded could not be started, said plainly. Empty: every research
#: direction can now be started from a briefing. Kept because a lab name that reaches
#: here without being one of the above is a bug, and it must say so rather than vanish.
NOT_DISPATCHABLE: dict[str, str] = {}

#: Mixes verified per day. The search is free — it happens on stored returns — so only
#: the confirmation simulations cost anything, and a handful of them is plenty.
MAX_MIXES = 20


class Dispatcher:
    """Executes an allocation: work queued, cores assigned, per desk."""

    def __init__(self, state: Any) -> None:
        self.state = state

    async def run(
        self,
        *,
        allocations: list[dict[str, Any]],
        scope: Tuple4,
        target: int,
        replace: bool = True,
    ) -> dict[str, Any]:
        """Queue every desk that can be queued, and say what happened to the rest."""
        if not allocations:
            return {"day": today(), "started": [], "queued": 0, "skipped": 0}

        day = today()
        if replace:
            await self._clear(day)
        # Split the day's allowance in proportion to the cores each desk was given: a
        # desk holding half the machines can get through roughly half the work.
        total_cores = sum(max(1, int(a.get("cores") or 1)) for a in allocations) or 1
        started: list[dict[str, Any]] = []

        for index, entry in enumerate(allocations):
            lab = str(entry["lab"])
            cores = max(1, int(entry.get("cores") or 1))
            share = max(1, round(target * cores / total_cores))
            task = f"{lab}-{day}-{index + 1}"

            if lab not in DISPATCHABLE:
                started.append(
                    {
                        **entry,
                        "task": task,
                        "queued": 0,
                        "started": False,
                        "note": NOT_DISPATCHABLE.get(
                            lab, "This desk cannot be started automatically yet."
                        ),
                    }
                )
                continue

            # Tuning is the exception to everything below. It does not answer with a
            # list of simulations — it drives its own optimiser across several rounds —
            # so it is *started* rather than queued.
            if lab == "deepen":
                try:
                    queued = await self._start_deepen(scope, share, task, cores)
                except Exception as exc:
                    log.warning("dispatch.desk_failed", lab=lab, error=str(exc)[:200])
                    started.append(
                        {
                            **entry,
                            "task": task,
                            "queued": 0,
                            "started": False,
                            "note": str(exc)[:200],
                        }
                    )
                    continue
                started.append(
                    {
                        **entry,
                        "task": task,
                        "queued": queued,
                        "asked": share,
                        "short": queued < share,
                        "started": bool(queued),
                        "note": (
                            None
                            if queued
                            else "Nothing here has scored well enough to be worth tuning yet."
                        ),
                    }
                )
                continue

            try:
                requests = await self._requests_for(lab, scope, share)
            except Exception as exc:
                # One desk failing must not cost the others their day.
                log.warning("dispatch.desk_failed", lab=lab, error=str(exc)[:200])
                started.append(
                    {**entry, "task": task, "queued": 0, "started": False, "note": str(exc)[:200]}
                )
                continue

            if not requests:
                started.append(
                    {
                        **entry,
                        "task": task,
                        "queued": 0,
                        "started": False,
                        "note": "There was nothing for this desk to run in this market today.",
                    }
                )
                continue

            outcome = await self.state.engine.enqueue(requests, task=task)
            await self.state.engine.set_quota(task, cores)
            started.append(
                {
                    **entry,
                    "task": task,
                    "queued": len(outcome["queued"]),
                    "skipped": len(outcome["skipped"]),
                    "asked": share,
                    "short": len(outcome["queued"]) < share,
                    "started": True,
                }
            )

        queued = sum(int(s.get("queued") or 0) for s in started)

        # Several desks depend on what the consultant already owns — Relocate needs a
        # second market, Harden needs a result worth checking — so a legitimate
        # allocation can come up far short on day one. The allowance expires at midnight
        # either way, so the shortfall goes to the one desk with effectively unlimited
        # capacity rather than being left on the table. Drawn with its own levers, so it
        # is a different piece of research and not a second copy of the first sweep.
        topped_up = 0
        if queued < target:
            shortfall = target - queued
            try:
                extra = await self._requests_for("sweep", scope, shortfall)
            except Exception:
                log.warning("dispatch.topup_failed", day=day, exc_info=True)
                extra = []
            if extra:
                task = f"sweep-{day}-{len(allocations) + 1}"
                outcome = await self.state.engine.enqueue(extra, task=task)
                topped_up = len(outcome["queued"])
                await self.state.engine.set_quota(task, self.state.engine.slots)
                started.append(
                    {
                        "lab": "sweep",
                        "name": "Go exploring",
                        "cores": self.state.engine.slots,
                        "task": task,
                        "queued": topped_up,
                        "asked": shortfall,
                        "started": True,
                        "topUp": True,
                        "why": (
                            "The other desks could not fill the day on their own, so the "
                            "rest of today's allowance goes here rather than expiring."
                        ),
                    }
                )
                queued += topped_up

        running = sum(1 for s in started if s["started"])
        log.info("dispatch.done", day=day, desks=running, queued=queued, topped_up=topped_up)
        return {
            "day": day,
            "scope": scope.model_dump(by_alias=True),
            "started": started,
            "queued": queued,
            "skipped": sum(int(s.get("skipped") or 0) for s in started),
            "desksStarted": running,
            "desksAllocated": len(allocations),
            "toppedUp": topped_up,
            #: True when even the top-up could not reach the target. The market ran out
            #: of usable data before the day did, and saying so beats a silent shortfall.
            "short": queued < target,
        }

    async def _clear(self, day: str) -> int:
        """Drop what a previous run of today's plan left queued.

        The day plan's own ``stop_all`` only reaches rows it recorded as tracks, and
        nothing here is a track — so without this, pressing the button twice queues the
        day twice and spends the allowance at double the rate the screen reports. Scoped
        to today's dispatch tasks so a harvest or a study the consultant started by hand
        is not silently thrown away.
        """
        status = await self.state.engine.status()
        mine = [
            task
            for task in (status.get("queued") or {})
            if any(str(task).startswith(f"{lab}-{day}-") for lab in DISPATCHABLE)
        ]
        dropped = 0
        for task in mine:
            dropped += await self.state.engine.drop_queued(task)
            await self.state.engine.set_quota(task, 0, enabled=False)

        # A study keeps asking for rounds on its own, so one left running from an earlier
        # press of the same day's button would spend the allowance alongside the new
        # plan rather than instead of it.
        try:
            for study, _counts in await self.state.optimizer.list_all():
                if str(study.task or "").startswith(f"deepen-{day}-") and (
                    study.status == StudyStatus.RUNNING
                ):
                    await self.state.optimizer.set_status(study.id, StudyStatus.PAUSED)
                    dropped += 1
        except Exception:
            log.warning("dispatch.pause_studies_failed", day=day, exc_info=True)

        # A day plan started from "Run my day" is the same day's allowance, so it is
        # replaced too.
        dropped += await self.state.planner.stop_all()
        if dropped:
            log.info("dispatch.cleared", day=day, dropped=dropped, tasks=len(mine))
        return dropped

    async def _requests_for(self, lab: str, scope: Tuple4, share: int) -> list[SimulationRequest]:
        """Ask one desk for its share of the day."""
        state = self.state

        if lab == "sweep":
            # Drawn rather than fixed: this is where the spread across five hundred
            # consultants comes from, and two people funding Sweep on the same day must
            # not receive the same fields. See DayPlanner.suggest.
            proposal = state.planner.suggest(tracks=1, target=share)[0]
            harvest = await state.harvester.harvest(scope, target=share, **proposal["recipe"])
            return harvest.requests

        if lab == "pair":
            # Unseeded on purpose: the draw varies by day and market, which is what stops
            # five hundred consultants funding this on one morning from drawing one book.
            return (await state.pairer.plan(scope=scope, target=share))["requests"]

        if lab == "invent":
            return (await state.inventor.plan(scope=scope, target=share))["requests"]

        if lab == "combine":
            return await self._mixes(scope, share)

        if lab == "repair":
            return (await state.repairer.plan(scope=scope, target=share))["requests"]

        if lab == "relocate":
            return (await state.relocator.plan(origin=scope, target=share))["requests"]

        if lab == "harden":
            return (await state.hardener.plan(scope=scope, target=share))["requests"]

        if lab == "diversify":
            plan = await state.diversifier.plan(scope=scope, target=share)
            harvest = plan.get("harvest")
            return harvest.requests if harvest else []

        return []

    async def _mixes(self, scope: Tuple4, share: int) -> list[SimulationRequest]:
        """Mixes worth verifying, found on stored returns rather than by simulating.

        Only the confirmation costs anything, so the cap is small on purpose: the value
        here is in the search, and the search is free.
        """
        wanted = min(share, MAX_MIXES)
        result = await self.state.mixer.candidates(
            region=scope.region,
            delay=scope.delay,
            universe=scope.universe,
            instrument_type=scope.instrument_type,
            sizes=[2, 3],
            min_sharpe=1.0,
            limit=wanted * 2,
        )

        requests: list[SimulationRequest] = []
        for group in result["groups"]:
            expression = group.get("expression")
            # A mix the platform would reject on self-correlation is not worth
            # confirming; that verdict is computable here, from returns already stored.
            passes = (group.get("selfCorrelation") or {}).get("passes", True)
            if not expression or not passes:
                continue
            requests.append(
                SimulationRequest(
                    type="REGULAR",
                    settings=SimulationSettings(
                        instrumentType=scope.instrument_type,
                        region=scope.region,
                        delay=scope.delay,
                        universe=scope.universe,
                        # Shared by every member by construction, and the identity that
                        # licensed predicting the mix in the first place.
                        neutralization=str(group.get("neutralization") or "SUBINDUSTRY"),
                        decay=0,
                        truncation=0.08,
                    ),
                    regular=expression,
                )
            )
            if len(requests) >= wanted:
                break
        return requests

    async def _start_deepen(self, scope: Tuple4, share: int, task: str, cores: int) -> int:
        """Turn the best alphas here into studies, and set them running.

        Returns how many simulations the first rounds actually asked for. One seed that
        cannot become a study — a field no longer in the synced catalog, a name already
        taken — must not cost the other seeds theirs.
        """
        state = self.state
        chosen = await deepen.seeds(state.alphas, scope)
        if not chosen:
            return 0

        per_seed = max(deepen.BATCH, share // len(chosen))
        asked = 0
        for index, row in enumerate(chosen):
            name = f"{task}-{index + 1}"
            try:
                study = await state.optimizer.create(
                    name=name,
                    template_source=deepen.template_for(row, name=name),
                    batch_size=deepen.BATCH,
                    max_trials=per_seed,
                    task=task,
                )
                await state.optimizer.set_status(study.id, StudyStatus.RUNNING)
                asked += int((await state.optimizer.advance(study.id)).get("asked") or 0)
            except Exception:
                log.warning("dispatch.seed_not_tuned", alpha=row.get("alpha_id"), exc_info=True)

        if asked:
            await state.engine.set_quota(task, cores)
        log.info("dispatch.deepen_started", task=task, seeds=len(chosen), asked=asked)
        return asked
