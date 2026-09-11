"""Packing simulations into multi-simulation batches.

``POST /simulations`` accepts an array of 2-10 simulation objects, but the platform
requires every child of one batch to agree on five fields: ``type``, ``instrumentType``,
``region``, ``delay`` and ``language``. Children *may* differ in universe,
neutralization, decay, truncation and expression.

(See ``docs/worldquantbrain/brain-api/brain-api.md`` and
``docs/worldquantbrain/consultant-information/multi-alpha-simulation.md``.)

That constraint is the whole reason this module exists. Throughput is not "80 at a
time" — it is "8 batches at a time, each of up to 10 simulations that happen to share a
5-tuple". A sweep that varies region or delay fragments into many small batches and gets
nowhere near 80; a sweep that varies universe, decay and expression packs perfectly.

This module is deliberately pure: no database, no HTTP. It answers one question — given
these pending items and this many free slots, what should be sent?
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

#: A multi-simulation may carry at most this many children.
MAX_BATCH = 10
#: Below this the platform expects a bare object, not an array of one.
MIN_ARRAY = 2


@dataclass(frozen=True, slots=True)
class BatchKey:
    """The five fields every child of one batch must share."""

    sim_type: str
    instrument_type: str
    region: str
    delay: int
    language: str

    def describe(self) -> str:
        return (
            f"{self.sim_type} {self.instrument_type} {self.region} "
            f"delay {self.delay} {self.language}"
        )


@dataclass(frozen=True, slots=True)
class WorkItem:
    """One queued simulation, identified by its local record id."""

    record_id: int
    key: BatchKey
    task: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Batch:
    """A set of items that may legally be submitted together."""

    key: BatchKey
    task: str
    items: tuple[WorkItem, ...]

    @property
    def size(self) -> int:
        return len(self.items)

    @property
    def record_ids(self) -> list[int]:
        return [item.record_id for item in self.items]

    def wire_payload(self) -> Any:
        """The body for ``POST /simulations``.

        A single item is sent as a bare object. The platform's array form is documented
        as length 2-10, so wrapping one simulation in an array would be sending
        something the contract does not describe.
        """
        payloads = [item.payload for item in self.items]
        return payloads if len(payloads) >= MIN_ARRAY else payloads[0]


def key_of(payload: dict[str, Any]) -> BatchKey:
    """Derive the batch key from a simulation request body."""
    settings = payload.get("settings") or {}
    return BatchKey(
        sim_type=str(payload.get("type", "REGULAR")),
        instrument_type=str(settings.get("instrumentType", "EQUITY")),
        region=str(settings.get("region", "")),
        delay=int(settings.get("delay", 1)),
        language=str(settings.get("language", "FASTEXPR")),
    )


def group_by_key(items: Iterable[WorkItem]) -> dict[BatchKey, list[WorkItem]]:
    """Bucket items by the 5-tuple, preserving queue order within each bucket."""
    groups: dict[BatchKey, list[WorkItem]] = defaultdict(list)
    for item in items:
        groups[item.key].append(item)
    return dict(groups)


def pack(
    items: Sequence[WorkItem],
    *,
    free_slots: int,
    max_batch: int = MAX_BATCH,
    task_capacity: dict[str, int] | None = None,
) -> list[Batch]:
    """Choose what to submit next.

    Groups items by batch key and fills up to ``free_slots`` batches of at most
    ``max_batch`` each.

    Fullest groups go first. With 8 slots and a queue holding one group of 30 and six
    groups of 1, sending the big group's three full batches moves 30 simulations while
    sending the singletons moves 6 — so the greedy choice is also the right one.

    ``task_capacity`` caps how many batches each named task may be given in this round;
    a task absent from the mapping is unconstrained. Items are never mixed across tasks,
    so a batch always belongs to exactly one task and stays attributable.
    """
    if free_slots <= 0 or not items:
        return []

    remaining = dict(task_capacity or {})
    batches: list[Batch] = []

    # Group by (task, key): a batch must share the 5-tuple *and* belong to one task.
    groups: dict[tuple[str, BatchKey], list[WorkItem]] = defaultdict(list)
    for item in items:
        groups[(item.task, item.key)].append(item)

    # Slice each group into batch-sized chunks up front, so ordering can consider the
    # chunks themselves rather than the groups they came from.
    chunks: list[tuple[str, BatchKey, list[WorkItem]]] = []
    for (task, key), members in groups.items():
        for start in range(0, len(members), max_batch):
            chunks.append((task, key, members[start : start + max_batch]))

    # Fullest chunks first; ties broken by the earliest queued item so a small group
    # cannot be starved indefinitely behind a steadily refilled large one.
    chunks.sort(key=lambda c: (-len(c[2]), c[2][0].record_id))

    for task, key, members in chunks:
        if len(batches) >= free_slots:
            break
        if task in remaining:
            if remaining[task] <= 0:
                continue
            remaining[task] -= 1
        batches.append(Batch(key=key, task=task, items=tuple(members)))

    return batches


def allocate_slots(
    free_slots: int,
    *,
    demand: dict[str, int],
    quotas: dict[str, int],
    in_flight: dict[str, int] | None = None,
) -> dict[str, int]:
    """Divide free slots between tasks that want them.

    ``demand`` is how many batches each task could fill right now, ``quotas`` the
    configured ceiling on *concurrent* slots per task, and ``in_flight`` how many each
    already holds. A task with no quota is unconstrained.

    Allocation is round-robin rather than proportional: with two tasks wanting work and
    three slots free, one gets two and the other one, instead of a large sweep taking
    everything and a single manual experiment waiting behind it.
    """
    if free_slots <= 0:
        return {}

    running = in_flight or {}
    headroom: dict[str, int] = {}
    for task, wanted in demand.items():
        if wanted <= 0:
            continue
        limit = quotas.get(task)
        available = wanted if limit is None else max(0, limit - running.get(task, 0))
        capped = min(wanted, available)
        if capped > 0:
            headroom[task] = capped

    allocation: dict[str, int] = defaultdict(int)
    granted = 0
    # Deterministic order so the same queue always allocates the same way.
    tasks = sorted(headroom)
    while granted < free_slots and headroom:
        progressed = False
        for task in tasks:
            if granted >= free_slots:
                break
            if headroom.get(task, 0) <= 0:
                continue
            allocation[task] += 1
            headroom[task] -= 1
            granted += 1
            progressed = True
        if not progressed:
            break

    return dict(allocation)
