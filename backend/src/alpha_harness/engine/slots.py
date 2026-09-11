"""The batch engine: queue, pack, submit, expand.

Throughput on BRAIN is eight *concurrent simulations*, each of which may be a
multi-simulation carrying up to ten children — so eighty at once, but only when the work
packs. Children of one batch must share a 5-tuple (see :mod:`.packer`), so the engine's
job is to hold a queue, group it, and keep the slots full without ever losing an id.

Responsibilities are split deliberately:

* :mod:`.packer` decides *what* to send — pure, no I/O.
* :class:`~alpha_harness.engine.tracker.SimulationTracker` owns the atomic write of a
  platform id and the polling that follows.
* This module owns the queue, the slot accounting and the expansion of a finished
  batch into its children.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, update

from ..brain.endpoints import BrainEndpoints
from ..brain.errors import BrainDailyLimitReached, BrainError
from ..brain.filters import PLATFORM_TZ
from ..brain.schemas import SimulationRequest
from ..db.models import DedupEntry, SimStatus, SimulationRecord, TaskQuota, utcnow
from ..db.sqlite import Database
from .dedup import hash_payload
from .packer import MAX_BATCH, Batch, WorkItem, allocate_slots, key_of, pack
from .tracker import SimulationTracker, SubmissionFailed, serialise

log = structlog.get_logger(__name__)

#: Concurrent simulations the platform allows. Accounts without MULTI_SIMULATION get
#: the same number of slots but a batch size of one.
DEFAULT_SLOTS = 8

#: How often the engine looks for work.
TICK_SECONDS = 2.0

ChangeHook = Callable[[list[dict[str, Any]]], Awaitable[None] | None]


class BatchEngine:
    """Keeps the concurrent slots full from a local queue."""

    def __init__(
        self,
        db: Database,
        endpoints: BrainEndpoints,
        tracker: SimulationTracker,
        *,
        slots: int = DEFAULT_SLOTS,
        on_change: ChangeHook | None = None,
    ) -> None:
        self.db = db
        self.endpoints = endpoints
        self.tracker = tracker
        self.slots = slots
        self._on_change = on_change
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        #: Set when the daily cap is hit. Nothing is submitted until it clears, because
        #: retrying before the US-Eastern reset cannot succeed.
        self._daily_limit_hit = False
        #: The US-Eastern date the cap was hit on; the flag clears once that date passes.
        self._limit_day: date | None = None
        self._tick_lock = asyncio.Lock()
        #: Batch size, reduced to 1 for accounts without MULTI_SIMULATION.
        self.max_batch = MAX_BATCH

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="batch-engine")
        log.info("engine.started", slots=self.slots, max_batch=self.max_batch)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        log.info("engine.stopped")

    def configure_from_permissions(self, permissions: list[str]) -> None:
        """Batching needs MULTI_SIMULATION; without it every batch is a single run."""
        self.max_batch = MAX_BATCH if "MULTI_SIMULATION" in permissions else 1

    def clear_daily_limit(self) -> None:
        """Called when a new US-Eastern day starts, or by the user."""
        self._daily_limit_hit = False

    @property
    def daily_limit_hit(self) -> bool:
        return self._daily_limit_hit

    # -- queueing --------------------------------------------------------

    async def enqueue(
        self,
        requests: list[SimulationRequest],
        *,
        task: str = "manual",
        skip_duplicates: bool = True,
    ) -> dict[str, Any]:
        """Accept work. Returns what was queued and what was skipped as a duplicate.

        Duplicates are checked before anything is sent: re-running an identical alpha
        spends daily quota and produces an alpha that already exists.

        ``outcomes`` carries one entry per request, in the order given, because a caller
        that generated the requests needs to know which of *its* items became which row.
        The optimizer depends on this: a trial matched to an existing alpha can be scored
        immediately, so the search learns from a repeated point without paying for it.
        """
        queued: list[int] = []
        skipped: list[dict[str, Any]] = []
        outcomes: list[dict[str, Any]] = []

        async with self.db.session() as session:
            for index, request in enumerate(requests):
                payload = request.to_wire()
                request_hash = hash_payload(payload)
                settings = request.settings

                if skip_duplicates:
                    existing = await session.get(DedupEntry, request_hash)
                    if existing is not None:
                        record = SimulationRecord(
                            request_hash=request_hash,
                            payload=payload,
                            expression=request.regular or request.combo or request.selection,
                            sim_type=str(request.type),
                            instrument_type=settings.instrument_type,
                            region=settings.region,
                            delay=settings.delay,
                            language=settings.language,
                            universe=settings.universe,
                            task=task,
                            status=SimStatus.SKIPPED,
                            alpha_id=existing.alpha_id,
                            message=(
                                "Identical alpha already simulated; reused the existing result."
                            ),
                            finished_at=utcnow(),
                        )
                        session.add(record)
                        await session.flush()
                        skipped.append({"alphaId": existing.alpha_id, "hash": request_hash})
                        outcomes.append(
                            {
                                "index": index,
                                "recordId": record.id,
                                "status": str(SimStatus.SKIPPED),
                                "alphaId": existing.alpha_id,
                                "hash": request_hash,
                            }
                        )
                        continue

                record = SimulationRecord(
                    request_hash=request_hash,
                    payload=payload,
                    expression=request.regular or request.combo or request.selection,
                    sim_type=str(request.type),
                    instrument_type=settings.instrument_type,
                    region=settings.region,
                    delay=settings.delay,
                    language=settings.language,
                    universe=settings.universe,
                    task=task,
                    status=SimStatus.QUEUED,
                )
                session.add(record)
                await session.flush()
                queued.append(record.id)
                outcomes.append(
                    {
                        "index": index,
                        "recordId": record.id,
                        "status": str(SimStatus.QUEUED),
                        "alphaId": None,
                        "hash": request_hash,
                    }
                )

        log.info("engine.enqueued", task=task, queued=len(queued), skipped=len(skipped))
        await self._notify()
        return {"queued": queued, "skipped": skipped, "outcomes": outcomes}

    async def drop_queued(self, task: str | None = None) -> int:
        """Discard queued work that has not been sent anywhere yet.

        Safe by construction: a QUEUED row has no platform id because nothing was ever
        submitted for it.
        """
        async with self.db.session() as session:
            statement = (
                update(SimulationRecord)
                .where(SimulationRecord.status == SimStatus.QUEUED)
                .values(
                    status=SimStatus.CANCELLED,
                    finished_at=utcnow(),
                    message="Removed from the queue before it was submitted.",
                )
            )
            if task is not None:
                statement = statement.where(SimulationRecord.task == task)
            result = await session.execute(statement)
            dropped = result.rowcount or 0

        if dropped:
            log.info("engine.queue_dropped", task=task, count=dropped)
            await self._notify()
        return dropped

    # -- quotas ----------------------------------------------------------

    async def set_quota(self, name: str, max_slots: int, *, enabled: bool = True) -> None:
        async with self.db.session() as session:
            row = await session.get(TaskQuota, name)
            if row is None:
                session.add(TaskQuota(name=name, max_slots=max_slots, enabled=enabled))
            else:
                row.max_slots = max_slots
                row.enabled = enabled

    async def quotas(self) -> dict[str, int]:
        async with self.db.session() as session:
            rows = (await session.execute(select(TaskQuota))).scalars()
            return {r.name: (r.max_slots if r.enabled else 0) for r in rows}

    async def status(self) -> dict[str, Any]:
        """Everything the matrix header needs in one call."""
        async with self.db.session() as session:
            in_flight = await self._in_flight_by_task(session)
            queued_rows = await session.execute(
                select(SimulationRecord.task, func.count())
                .where(SimulationRecord.status == SimStatus.QUEUED)
                .group_by(SimulationRecord.task)
            )
            queued = dict(queued_rows.all())

        used = sum(in_flight.values())
        return {
            "slots": self.slots,
            "maxBatch": self.max_batch,
            "slotsUsed": used,
            "slotsFree": max(0, self.slots - used),
            "queued": queued,
            "queuedTotal": sum(queued.values()),
            "inFlight": in_flight,
            "quotas": await self.quotas(),
            "dailyLimitHit": self._daily_limit_hit,
        }

    # -- the loop --------------------------------------------------------

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("engine.tick_failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=TICK_SECONDS)
            except TimeoutError:
                continue

    async def tick(self) -> int:
        """One scheduling round. Returns how many batches were submitted.

        Free slots are filled before finished batches are read back. Reading back costs a
        throttled request per child, and slots left idle while that drains are capacity
        the platform offered and nobody used.
        """
        # The background loop and the manual tick route share this: two concurrent rounds
        # would read the same QUEUED rows and submit them twice.
        async with self._tick_lock:
            submitted = await self._fill_slots()
            await self._expand_finished_batches()
        return submitted

    async def _fill_slots(self) -> int:
        if self._daily_limit_hit and self._limit_day != _platform_today():
            # A new US-Eastern day: the quota is back.
            self.clear_daily_limit()
        if self._daily_limit_hit:
            return 0

        async with self.db.session() as session:
            in_flight = await self._in_flight_by_task(session)
            free = self.slots - sum(in_flight.values())
            if free <= 0:
                return 0

            queued = (
                (
                    await session.execute(
                        select(SimulationRecord)
                        .where(SimulationRecord.status == SimStatus.QUEUED)
                        .order_by(SimulationRecord.id)
                    )
                )
                .scalars()
                .all()
            )
            if not queued:
                return 0

            items = [
                WorkItem(
                    record_id=r.id,
                    key=key_of(r.payload or {}),
                    task=r.task,
                    payload=r.payload or {},
                )
                for r in queued
            ]

        quotas = await self.quotas()
        demand = _demand_by_task(items, self.max_batch)
        allocation = allocate_slots(free, demand=demand, quotas=quotas, in_flight=in_flight)
        if not allocation:
            return 0

        batches = pack(
            items,
            free_slots=free,
            max_batch=self.max_batch,
            task_capacity=allocation,
        )

        submitted = 0
        for batch in batches:
            if self._daily_limit_hit:
                break
            if await self._submit(batch):
                submitted += 1

        if submitted:
            await self._notify()
        return submitted

    # -- submission ------------------------------------------------------

    async def _submit(self, batch: Batch) -> bool:
        """Send one batch, keeping the id-before-request discipline.

        A single-item batch reuses the tracker's ordinary path so there is exactly one
        implementation of "record the id atomically". A real batch creates a parent row
        first — the parent is the only cancellable handle.
        """
        if batch.size == 1:
            return await self._submit_single(batch.items[0])
        return await self._submit_batch(batch)

    async def _submit_single(self, item: WorkItem) -> bool:
        request = SimulationRequest.model_validate(item.payload)
        try:
            await self.tracker.submit(request, task=item.task, record_id=item.record_id)
            return True
        except BrainDailyLimitReached:
            await self._on_daily_limit()
            return False
        except SubmissionFailed as exc:
            # The tracker wraps platform errors, so the daily cap arrives as a cause
            # rather than as itself. Missing it here would march the whole queue into
            # a limit that cannot be satisfied until tomorrow.
            if isinstance(exc.cause, BrainDailyLimitReached):
                await self._on_daily_limit()
            else:
                log.warning("engine.single_failed", record_id=item.record_id, error=str(exc))
            return False
        except Exception as exc:
            log.warning("engine.single_failed", record_id=item.record_id, error=str(exc))
            return False

    async def _submit_batch(self, batch: Batch) -> bool:
        first = batch.items[0].payload
        settings = first.get("settings") or {}

        # 1. Parent row, committed before anything leaves the process.
        async with self.db.session() as session:
            parent = SimulationRecord(
                request_hash=hash_payload([i.payload for i in batch.items]),
                payload={"children": [i.payload for i in batch.items]},
                expression=f"{batch.size} simulations",
                sim_type=batch.key.sim_type,
                instrument_type=batch.key.instrument_type,
                region=batch.key.region,
                delay=batch.key.delay,
                language=batch.key.language,
                universe=settings.get("universe"),
                task=batch.task,
                status=SimStatus.PENDING,
                is_batch=True,
            )
            session.add(parent)
            await session.flush()
            parent_id = parent.id

            await session.execute(
                update(SimulationRecord)
                .where(SimulationRecord.id.in_(batch.record_ids))
                .values(status=SimStatus.PENDING, parent_record_id=parent_id)
            )

        # 2. Send it.
        requests = [SimulationRequest.model_validate(i.payload) for i in batch.items]
        try:
            response = await self.endpoints.create_simulation(requests)
        except BrainDailyLimitReached:
            # Back to the queue: the work is still wanted, and runs after the reset.
            await self._fail_batch(
                parent_id, batch, "Daily simulation limit reached.", requeue=True
            )
            await self._on_daily_limit()
            return False
        except BrainError as exc:
            await self._fail_batch(parent_id, batch, exc.message, requeue=exc.retryable)
            return False

        # 3. Record the parent id immediately — it is the only way to cancel the batch.
        from .tracker import extract_simulation_id

        platform_id = extract_simulation_id(response.location)
        if platform_id is None:
            await self._fail_batch(
                parent_id,
                batch,
                "BRAIN accepted the batch but returned no id, so it cannot be cancelled "
                "from here. Check the platform.",
                requeue=False,
                status=SimStatus.ORPHANED,
            )
            return False

        now = utcnow()
        async with self.db.session() as session:
            await session.execute(
                update(SimulationRecord)
                .where(SimulationRecord.id == parent_id)
                .values(platform_id=platform_id, status=SimStatus.RUNNING, submitted_at=now)
            )
            await session.execute(
                update(SimulationRecord)
                .where(SimulationRecord.id.in_(batch.record_ids))
                .values(status=SimStatus.RUNNING, submitted_at=now)
            )
            await self.tracker.record_quota(session, response.rate_limit)

        self.tracker.watch(parent_id)
        log.info(
            "engine.batch_running",
            parent=parent_id,
            platform_id=platform_id,
            size=batch.size,
            task=batch.task,
            key=batch.key.describe(),
        )
        return True

    async def _fail_batch(
        self,
        parent_id: int,
        batch: Batch,
        message: str,
        *,
        requeue: bool,
        status: SimStatus = SimStatus.REJECTED,
    ) -> None:
        """Mark a batch that never started.

        Retryable failures put the children back in the queue; a rejection that will
        never succeed marks them so, rather than looping forever on the same payload.
        """
        child_status = SimStatus.QUEUED if requeue else status
        async with self.db.session() as session:
            await session.execute(
                update(SimulationRecord)
                .where(SimulationRecord.id == parent_id)
                .values(status=status, message=message, finished_at=utcnow())
            )
            values: dict[str, Any] = {"status": child_status, "parent_record_id": None}
            if not requeue:
                values["message"] = message
                values["finished_at"] = utcnow()
            await session.execute(
                update(SimulationRecord)
                .where(SimulationRecord.id.in_(batch.record_ids))
                .values(**values)
            )
        log.warning("engine.batch_failed", parent=parent_id, requeued=requeue, message=message)
        await self._notify()

    async def _on_daily_limit(self) -> None:
        self._daily_limit_hit = True
        self._limit_day = _platform_today()
        log.warning("engine.daily_limit_reached")
        await self._notify()

    # -- child expansion -------------------------------------------------

    async def _expand_finished_batches(self) -> None:
        """Resolve the children of any completed batch.

        Child ids only exist once the parent finishes. Rather than trusting positional
        order, each child simulation is fetched and matched back to the row that
        requested it by the settings that vary within a batch — the fields that could
        differ are exactly what identifies which request produced which alpha.
        """
        async with self.db.session() as session:
            parents = (
                (
                    await session.execute(
                        select(SimulationRecord).where(
                            SimulationRecord.is_batch.is_(True),
                            # Failed and cancelled parents too: their children would
                            # otherwise stay RUNNING, holding counts, forever.
                            SimulationRecord.status.in_(
                                [
                                    SimStatus.COMPLETE,
                                    SimStatus.WARNING,
                                    SimStatus.ERROR,
                                    SimStatus.FAILED,
                                    SimStatus.TIMEOUT,
                                    SimStatus.CANCELLED,
                                ]
                            ),
                            SimulationRecord.children_expanded.is_(False),
                        )
                    )
                )
                .scalars()
                .all()
            )

        # One per round, so free slots are refilled between read-backs rather than waiting
        # for a whole backlog of them to drain.
        for parent in parents[:1]:
            await self._expand(parent)

    async def _expand(self, parent: SimulationRecord) -> None:
        child_ids: list[str] = list(parent.child_ids or [])
        async with self.db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(SimulationRecord)
                        .where(SimulationRecord.parent_record_id == parent.id)
                        .order_by(SimulationRecord.id)
                    )
                )
                .scalars()
                .all()
            )
            records = list(rows)

        if not child_ids:
            # The platform reported no children. Say so on the rows rather than
            # leaving them stuck as RUNNING forever.
            ended_badly = parent.status not in (SimStatus.COMPLETE, SimStatus.WARNING)
            await self._finish_children(
                records,
                {},
                fallback_message=(
                    parent.message or f"The batch ended {parent.status} before it ran."
                )
                if ended_badly
                else "The batch finished but BRAIN listed no child simulations.",
                child_status=SimStatus.CANCELLED
                if parent.status == SimStatus.CANCELLED
                else SimStatus.ERROR,
            )
            await self._mark_expanded(parent.id)
            return

        resolved: dict[int, dict[str, Any]] = {}
        unmatched: list[tuple[str, dict[str, Any]]] = []

        for child_id in child_ids:
            try:
                child = await self.endpoints.get_simulation(child_id)
            except BrainError as exc:
                log.warning("engine.child_fetch_failed", child=child_id, error=str(exc))
                continue
            body = child.model_dump(by_alias=True)
            signature = _signature_of_simulation(body)
            match = next(
                (
                    r
                    for r in records
                    if r.id not in resolved and _signature_of_payload(r.payload or {}) == signature
                ),
                None,
            )
            if match is not None:
                resolved[match.id] = {"platform_id": child_id, "body": body}
            else:
                unmatched.append((child_id, body))

        # Anything the signature could not place falls back to submission order, which
        # is right in practice but is recorded so a wrong attribution is visible.
        leftovers = [r for r in records if r.id not in resolved]
        for record, (child_id, body) in zip(leftovers, unmatched, strict=False):
            resolved[record.id] = {"platform_id": child_id, "body": body, "positional": True}

        await self._finish_children(records, resolved)
        await self._mark_expanded(parent.id)
        log.info(
            "engine.batch_expanded",
            parent=parent.id,
            children=len(child_ids),
            matched=len(resolved),
        )
        await self._notify()

    async def _finish_children(
        self,
        records: list[SimulationRecord],
        resolved: dict[int, dict[str, Any]],
        *,
        fallback_message: str | None = None,
        child_status: SimStatus = SimStatus.ERROR,
    ) -> None:
        from ..brain.schemas import SimulationStatus
        from .tracker import STATUS_MAP

        async with self.db.session() as session:
            for record in records:
                found = resolved.get(record.id)
                if found is None:
                    await session.execute(
                        update(SimulationRecord)
                        .where(SimulationRecord.id == record.id)
                        .values(
                            status=child_status,
                            message=fallback_message
                            or "BRAIN did not report a result for this simulation.",
                            finished_at=utcnow(),
                        )
                    )
                    continue

                body = found["body"]
                raw = body.get("status")
                try:
                    platform_status = SimulationStatus(raw) if raw else None
                except ValueError:
                    platform_status = None
                local = STATUS_MAP.get(platform_status, SimStatus.COMPLETE)

                message = body.get("message")
                if found.get("positional"):
                    note = "Matched to this request by submission order."
                    message = f"{message} {note}" if message else note

                await session.execute(
                    update(SimulationRecord)
                    .where(SimulationRecord.id == record.id)
                    .values(
                        platform_id=found["platform_id"],
                        alpha_id=body.get("alpha"),
                        status=local,
                        platform_status=str(platform_status) if platform_status else None,
                        message=message,
                        progress=1.0,
                        finished_at=utcnow(),
                    )
                )

                if body.get("alpha"):
                    existing = await session.get(DedupEntry, record.request_hash)
                    if existing is None:
                        session.add(
                            DedupEntry(request_hash=record.request_hash, alpha_id=body["alpha"])
                        )

        # The tracker polls only the parent, which carries no alpha of its own. Without
        # this hand-off no batched alpha would reach the vault, so nothing batched could
        # ever be judged submittable.
        for found in resolved.values():
            if found["body"].get("alpha"):
                self.tracker.alpha_landed(found["body"]["alpha"])

    async def _mark_expanded(self, parent_id: int) -> None:
        async with self.db.session() as session:
            await session.execute(
                update(SimulationRecord)
                .where(SimulationRecord.id == parent_id)
                .values(children_expanded=True)
            )

    # -- helpers ---------------------------------------------------------

    async def _in_flight_by_task(self, session: Any) -> dict[str, int]:
        """Slots currently held, counted per task.

        Only parents and standalone simulations count — a batch's children ride in its
        single slot and must not be double-counted.
        """
        result = await session.execute(
            select(SimulationRecord.task, func.count())
            .where(
                SimulationRecord.status.in_([SimStatus.PENDING, SimStatus.RUNNING]),
                SimulationRecord.parent_record_id.is_(None),
            )
            .group_by(SimulationRecord.task)
        )
        return dict(result.all())

    async def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            records = await self.tracker.active()
            result = self._on_change([serialise(r) for r in records])
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception("engine.notify_failed")


def _platform_today() -> date:
    """Today on the platform's clock, which is what the daily quota resets on."""
    return datetime.now(PLATFORM_TZ).date()


def _demand_by_task(items: list[WorkItem], max_batch: int) -> dict[str, int]:
    """How many batches each task could fill right now."""
    from .packer import BatchKey

    counts: dict[tuple[str, BatchKey], int] = {}
    for item in items:
        counts[(item.task, item.key)] = counts.get((item.task, item.key), 0) + 1

    demand: dict[str, int] = {}
    for (task, _key), n in counts.items():
        demand[task] = demand.get(task, 0) + -(-n // max_batch)  # ceiling division
    return demand


def _signature_of_payload(payload: dict[str, Any]) -> tuple:
    """The fields that may vary between children of one batch."""
    settings = payload.get("settings") or {}
    return (
        settings.get("universe"),
        settings.get("neutralization"),
        settings.get("decay"),
        settings.get("truncation"),
        payload.get("regular") or payload.get("combo") or payload.get("selection"),
    )


def _signature_of_simulation(body: dict[str, Any]) -> tuple:
    """The same signature, read back off a simulation the platform returned."""
    settings = body.get("settings") or {}
    expression = body.get("regular") or body.get("combo") or body.get("selection")
    if isinstance(expression, dict):
        expression = expression.get("code")
    return (
        settings.get("universe"),
        settings.get("neutralization"),
        settings.get("decay"),
        settings.get("truncation"),
        expression,
    )
