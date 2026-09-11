"""Download the BRAIN data catalog into DuckDB.

Quantitative research starts from data, so the catalog is the foundation everything else
reads: templates pick fields from it, the LLM is given its category tree, and the Data
tab is a direct view of it.

Scope is one ``(instrumentType, region, delay, universe)`` tuple per run, because that is
exactly how the platform scopes ``/data-fields`` — a field that exists in USA/delay-1
may simply not exist in EUR/delay-0. Storing one row per field *per tuple* is what makes
"which fields are in both delays" a single query later.

**Fields arrive in one request.** ``GET /data-fields`` at ``version=3.0`` with all four
scope parameters returns the whole scope unpaginated, metrics included: all 85,569
fields of USA/delay-1/TOP3000 in about five seconds (checked live 2026-09-10). The paged
form refuses offsets at or beyond 10,000, which is what the per-dataset crawl this
replaced existed to work around.

Categories and datasets are still paged, but they are small.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select, update

from ..brain.endpoints import BrainEndpoints
from ..brain.errors import BrainError
from ..brain.schemas import DataCategory, DataField, DataSet
from ..db.duck import Catalog
from ..db.models import SyncRun, SyncStatus, utcnow
from ..db.sqlite import Database

log = structlog.get_logger(__name__)

#: Datasets buffered before a DuckDB write.
FLUSH_EVERY = 500

#: Scopes whose fields download at once in a full sync. The client's throttle still spaces
#: request starts one second apart; this only lets the slow (~5 s) responses overlap.
ALL_CONCURRENCY = 4

#: Tries per market for each stage of a full sync. The client already retries throttling
#: and server errors per request; this also covers an empty answer or a failed write.
SCOPE_ATTEMPTS = 3

#: Progress steps per market state in a full sync: its fields arrive (1), its details arrive (2).
#: A market that failed, or kept only its fields, is finished.
_STEPS = {"waiting": 0, "fetching": 0, "details": 1, "fields": 2, "done": 2, "failed": 2}

ALL_LABEL = "All BRAIN datasets"

ProgressHook = Callable[[dict[str, Any]], Awaitable[None] | None]


class SyncCancelled(RuntimeError):
    """The user stopped a running sync."""


@dataclass(frozen=True, slots=True)
class SyncTarget:
    """The scope of one download."""

    instrument_type: str
    region: str
    delay: int
    universe: str

    @property
    def params(self) -> dict[str, Any]:
        return {
            "instrumentType": self.instrument_type,
            "region": self.region,
            "delay": self.delay,
            "universe": self.universe,
        }

    @property
    def label(self) -> str:
        return f"{self.instrument_type}/{self.region}/D{self.delay}/{self.universe}"


#: The run row that stands for a whole-catalog sync rather than one scope.
ALL_TARGET = SyncTarget("EQUITY", "*", -1, "*")


class CatalogSync:
    """Runs and reports catalog downloads."""

    def __init__(
        self,
        db: Database,
        catalog: Catalog,
        endpoints: BrainEndpoints,
        *,
        on_progress: ProgressHook | None = None,
    ) -> None:
        self.db = db
        self.catalog = catalog
        self.endpoints = endpoints
        self._on_progress = on_progress
        self._cancels: dict[int, asyncio.Event] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._targets: dict[int, SyncTarget] = {}
        #: Live stage and scope counts of a running full sync, by run id.
        self._all: dict[int, dict[str, Any]] = {}

    # -- public API ------------------------------------------------------

    async def start(self, target: SyncTarget, *, restart: bool = False) -> SyncRun:
        """Begin a download in the background and return its run row immediately.

        A scope already downloading returns that run instead of starting a second one
        writing the same rows.
        """
        for run_id, running in self._targets.items():
            if running == target and run_id in self._tasks:
                live = await self.get_run(run_id)
                if live is not None:
                    return live

        run = await self._open_run(target, restart=restart)
        cancel = asyncio.Event()
        self._cancels[run.id] = cancel
        self._targets[run.id] = target

        task = asyncio.create_task(
            self._guarded(run.id, target.label, cancel, self._crawl(run.id, target, cancel)),
            name=f"sync-{run.id}",
        )
        self._tasks[run.id] = task

        def _done(_t: asyncio.Task[None], rid: int = run.id) -> None:
            self._tasks.pop(rid, None)
            self._targets.pop(rid, None)

        task.add_done_callback(_done)
        return run

    async def start_all(self, targets: list[SyncTarget]) -> dict[str, Any]:
        """Download every scope in the background and return its progress immediately.

        One run row stands for the whole download, so there is one progress feed and one
        cancel. Every market runs its own pipeline at once: its fields first (with dataset
        and category rows derived from them, so it is browsable immediately), then BRAIN's
        full dataset and category records. A market that fails is noted and skipped rather
        than ending the sync.
        """
        for run_id in list(self._all):
            if run_id in self._tasks:
                live = await self.get_run(run_id)
                if live is not None:
                    return self._payload(live)

        run = await self._open_run(ALL_TARGET, restart=False)
        cancel = asyncio.Event()
        self._cancels[run.id] = cancel
        self._targets[run.id] = ALL_TARGET
        self._all[run.id] = {
            "failed": [],
            # Per market, for the sync matrix: waiting, fetching, fields, details, done, failed.
            "markets": {
                t.label: {
                    "region": t.region,
                    "delay": t.delay,
                    "universe": t.universe,
                    "state": "waiting",
                    "fields": None,
                }
                for t in targets
            },
        }

        task = asyncio.create_task(
            self._guarded(run.id, ALL_LABEL, cancel, self._crawl_all(run.id, targets, cancel)),
            name=f"sync-all-{run.id}",
        )
        self._tasks[run.id] = task

        def _done(_t: asyncio.Task[None], rid: int = run.id) -> None:
            self._tasks.pop(rid, None)
            self._targets.pop(rid, None)
            self._all.pop(rid, None)

        task.add_done_callback(_done)
        return self._payload(run)

    async def reset_interrupted(self) -> int:
        """Mark runs left RUNNING by a crash as FAILED, so they stop reading as in progress.

        Called at startup, before anything can be running in this process.
        """
        async with self.db.session() as session:
            result = await session.execute(
                update(SyncRun)
                .where(SyncRun.status == SyncStatus.RUNNING)
                .values(
                    status=SyncStatus.FAILED,
                    error="The backend stopped during this sync.",
                    finished_at=utcnow(),
                )
            )
            return int(result.rowcount or 0)

    async def cancel(self, run_id: int) -> bool:
        event = self._cancels.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    async def get_run(self, run_id: int) -> SyncRun | None:
        async with self.db.session() as session:
            return await session.get(SyncRun, run_id)

    async def runs(self, limit: int = 50) -> list[SyncRun]:
        async with self.db.session() as session:
            result = await session.execute(
                select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)
            )
            return list(result.scalars())

    async def last_sync_by_tuple(self) -> list[dict[str, Any]]:
        """Most recent completed sync per tuple — drives the 'last synced' display."""
        async with self.db.session() as session:
            result = await session.execute(
                select(SyncRun)
                .where(SyncRun.status == SyncStatus.COMPLETE)
                .order_by(SyncRun.finished_at.desc())
            )
            seen: dict[tuple[str, str, int, str], dict[str, Any]] = {}
            for run in result.scalars():
                if run.tuple_key not in seen:
                    seen[run.tuple_key] = serialise_run(run)
            return list(seen.values())

    async def shutdown(self) -> None:
        for event in self._cancels.values():
            event.set()
        for task in list(self._tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # -- the download ----------------------------------------------------

    async def _guarded(
        self, run_id: int, label: str, cancel: asyncio.Event, work: Awaitable[None]
    ) -> None:
        try:
            await work
        except SyncCancelled:
            await self._finish(run_id, SyncStatus.CANCELLED)
            log.info("sync.cancelled", run_id=run_id, target=label)
        except asyncio.CancelledError:
            await self._finish(run_id, SyncStatus.CANCELLED, error="Backend shut down")
            raise
        except BrainError as exc:
            await self._finish(run_id, SyncStatus.FAILED, error=exc.message)
            log.warning("sync.failed", run_id=run_id, error=exc.message)
        except Exception as exc:
            await self._finish(run_id, SyncStatus.FAILED, error=str(exc))
            log.exception("sync.crashed", run_id=run_id)
        finally:
            self._cancels.pop(run_id, None)

    async def _crawl(self, run_id: int, target: SyncTarget, cancel: asyncio.Event) -> None:
        log.info("sync.started", run_id=run_id, target=target.label)
        await self._sync_categories(run_id, target, cancel)
        await self._sync_datasets(run_id, target, cancel)
        await self._sync_fields(run_id, target, cancel)
        await self._finish(run_id, SyncStatus.COMPLETE)
        log.info("sync.complete", run_id=run_id, target=target.label)

    async def _crawl_all(
        self, run_id: int, targets: list[SyncTarget], cancel: asyncio.Event
    ) -> None:
        progress = self._all[run_id]
        totals = {"fields": 0, "datasets": 0, "categories": 0}
        synced: list[SyncTarget] = []
        # Bounds how many markets hold a full field payload in memory at once; request pace is
        # the client's throttle either way.
        gate = asyncio.Semaphore(ALL_CONCURRENCY)

        async def report() -> None:
            await self._update(
                run_id,
                fields_synced=totals["fields"],
                datasets_synced=totals["datasets"],
                categories_synced=totals["categories"],
                error=_failures(progress),
            )
            await self._emit(run_id)

        async def pipeline(target: SyncTarget) -> None:
            market = progress["markets"][target.label]
            async with gate:
                _check(cancel)
                market["state"] = "fetching"
                await self._emit(run_id)

                async def store() -> tuple[int, int, int]:
                    started = time.perf_counter()
                    raw = await self.endpoints.list_data_fields_all(**target.params)
                    if not raw:
                        # The unpaginated form serves nothing for some scopes (the
                        # region-agnostic ALL) that the paged form does, dataset by dataset.
                        raw = await self._fields_by_dataset(target, cancel)
                    fetched = time.perf_counter()
                    _check(cancel)
                    if not raw:
                        raise RuntimeError("BRAIN returned no data fields")
                    now = utcnow()
                    rows = await asyncio.to_thread(_field_rows, raw, target, now)
                    datasets, categories = await asyncio.to_thread(_derived_rows, raw, target, now)
                    prepared = time.perf_counter()
                    scope = [target.instrument_type, target.region, target.delay, target.universe]
                    await self.catalog.replace_fields(scope, rows)
                    # Placeholders only fill gaps: a re-sync must not blank the Value Score,
                    # Coverage and counts that an earlier details step stored.
                    await self.catalog.upsert_datasets(datasets, overwrite=False)
                    await self.catalog.upsert_categories(categories, overwrite=False)
                    # Where a full sync's time goes: BRAIN's response, row building, or the
                    # single-writer DuckDB lock (which also waits on the other scopes' writes).
                    log.info(
                        "sync_all.scope_timing",
                        target=target.label,
                        fields=len(rows),
                        fetch_s=round(fetched - started, 2),
                        prepare_s=round(prepared - fetched, 2),
                        write_s=round(time.perf_counter() - prepared, 2),
                    )
                    return len(rows), len(datasets), len(categories)

                try:
                    fields, datasets, categories = await _retry(store, cancel, target.label)
                except SyncCancelled:
                    raise
                except Exception as exc:  # one market failing must not end the whole sync
                    market["state"] = "failed"
                    progress["failed"].append(f"{target.label}: {_reason(exc)}")
                    log.warning("sync_all.fields_failed", target=target.label, error=_reason(exc))
                    await report()
                    return

            # Browsable now; BRAIN's full dataset and category records follow straight away.
            totals["fields"] += fields
            totals["datasets"] += datasets
            totals["categories"] += categories
            market.update(state="details", fields=fields)
            synced.append(target)
            await report()

            async def details() -> None:
                now = utcnow()
                categories = await self.endpoints.list_data_categories(**target.params)
                category_rows = [
                    row
                    for category in categories
                    for row in (
                        _category_row(category, None, target, now),
                        *(
                            _category_row(sub, category.id, target, now)
                            for sub in category.subcategories
                        ),
                    )
                ]
                await self.catalog.upsert_categories(category_rows)
                dataset_rows: list[tuple] = []
                async for dataset in self.endpoints.iter_data_sets(**target.params):
                    _check(cancel)
                    dataset_rows.append(_dataset_row(dataset, target, now))
                await self.catalog.upsert_datasets(dataset_rows)

            try:
                await _retry(details, cancel, target.label)
                market["state"] = "done"
            except SyncCancelled:
                raise
            except Exception as exc:  # the market stays browsable on its derived rows
                market["state"] = "fields"
                progress["failed"].append(f"{target.label} details: {_reason(exc)}")
                log.warning("sync_all.details_failed", target=target.label, error=_reason(exc))
            await report()

        # Every market's pipeline at once. The client's throttle still starts requests one
        # second apart and backs everyone off together on a 429, so this only lets BRAIN's slow
        # responses overlap — and the long dataset-by-dataset crawl of the region-agnostic ALL
        # markets no longer holds up anyone else's details.
        await self._update(run_id, phase="fields")
        await self._emit(run_id)
        results = await asyncio.gather(*(pipeline(t) for t in targets), return_exceptions=True)
        _check(cancel)
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, SyncCancelled):
                raise result

        status = SyncStatus.COMPLETE if synced else SyncStatus.FAILED
        await self._finish(run_id, status, error=_failures(progress))
        log.info("sync_all.done", run_id=run_id, scopes=len(synced), failed=len(progress["failed"]))

    async def _fields_by_dataset(
        self, target: SyncTarget, cancel: asyncio.Event
    ) -> list[dict[str, Any]]:
        """A scope's fields through the paged form, one dataset at a time.

        Slow — one request per 50 fields at the client's pace — so used only when the
        unpaginated form answers with nothing.
        """
        raw: list[dict[str, Any]] = []
        async for dataset in self.endpoints.iter_data_sets(**target.params):
            _check(cancel)
            async for item in self.endpoints.iter_data_fields(
                **target.params, **{"dataset.id": dataset.id}
            ):
                raw.append(item)
        log.info("sync_all.crawled_by_dataset", target=target.label, fields=len(raw))
        return raw

    async def _sync_categories(
        self, run_id: int, target: SyncTarget, cancel: asyncio.Event
    ) -> None:
        _check(cancel)
        await self._update(run_id, phase="categories")
        categories = await self.endpoints.list_data_categories(**target.params)

        rows: list[tuple] = []
        now = utcnow()
        for category in categories:
            rows.append(_category_row(category, None, target, now))
            for sub in category.subcategories:
                rows.append(_category_row(sub, category.id, target, now))

        await self.catalog.upsert_categories(rows)
        await self._update(run_id, categories_synced=len(rows))
        await self._emit(run_id)

    async def _sync_datasets(self, run_id: int, target: SyncTarget, cancel: asyncio.Event) -> None:
        _check(cancel)
        await self._update(run_id, phase="datasets")

        rows: list[tuple] = []
        now = utcnow()
        count = 0
        async for dataset in self.endpoints.iter_data_sets(**target.params):
            _check(cancel)
            rows.append(_dataset_row(dataset, target, now))
            count += 1
            if len(rows) >= FLUSH_EVERY:
                await self.catalog.upsert_datasets(rows)
                rows.clear()
                await self._update(run_id, datasets_synced=count)
                await self._emit(run_id)

        if rows:
            await self.catalog.upsert_datasets(rows)
        await self._update(run_id, datasets_synced=count)
        await self._emit(run_id)

    async def _sync_fields(self, run_id: int, target: SyncTarget, cancel: asyncio.Event) -> None:
        """Replace the scope's fields with one unpaginated download."""
        _check(cancel)
        await self._update(run_id, phase="fields")
        await self._emit(run_id)

        raw = await self.endpoints.list_data_fields_all(**target.params)
        _check(cancel)
        if not raw:
            # Not a real answer for any market BRAIN offers. Keeping the last good
            # catalog beats replacing it with nothing.
            raise RuntimeError(f"BRAIN returned no data fields for {target.label}.")

        # Validating ~85k rows is CPU work; keep it off the event loop the engine runs on.
        rows = await asyncio.to_thread(_field_rows, raw, target, utcnow())
        await self._update(run_id, fields_expected=len(rows))
        await self.catalog.replace_fields(
            [target.instrument_type, target.region, target.delay, target.universe], rows
        )
        await self._update(run_id, fields_synced=len(rows))
        await self._emit(run_id)
        log.info("sync.fields_done", run_id=run_id, fields=len(rows))

    # -- run bookkeeping -------------------------------------------------

    async def _open_run(self, target: SyncTarget, *, restart: bool) -> SyncRun:
        if restart:
            await self.catalog.delete_tuple(
                target.instrument_type, target.region, target.delay, target.universe
            )
        async with self.db.session() as session:
            run = SyncRun(
                instrument_type=target.instrument_type,
                region=target.region,
                delay=target.delay,
                universe=target.universe,
                status=SyncStatus.RUNNING,
            )
            session.add(run)
            await session.flush()
            return run

    async def _update(self, run_id: int, **values: Any) -> None:
        async with self.db.session() as session:
            run = await session.get(SyncRun, run_id)
            if run is None:
                return
            for key, value in values.items():
                setattr(run, key, value)

    async def _finish(self, run_id: int, status: SyncStatus, *, error: str | None = None) -> None:
        await self._update(run_id, status=status, error=error, finished_at=utcnow())
        await self._emit(run_id)

    def _payload(self, run: SyncRun) -> dict[str, Any]:
        """A run's wire shape, plus live per-market states and progress while a full sync runs."""
        payload = serialise_run(run)
        progress = self._all.get(run.id)
        if progress:
            markets = list(progress["markets"].values())
            arrived = sum(m["state"] not in ("waiting", "fetching") for m in markets)
            payload |= {
                # "fields" while any market's fields are still on their way.
                "stage": "fields" if arrived < len(markets) else "details",
                "scopesDone": arrived,
                "scopesTotal": len(markets),
                "markets": markets,
                # One bar: each market's fields are half its share, its details the other half.
                "fraction": (
                    sum(_STEPS[m["state"]] for m in markets) / (2 * len(markets))
                    if markets
                    else 1.0
                ),
            }
        return payload

    async def _emit(self, run_id: int) -> None:
        if self._on_progress is None:
            return
        run = await self.get_run(run_id)
        if run is None:
            return
        try:
            result = self._on_progress(self._payload(run))
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            log.exception("sync.progress_hook_failed", run_id=run_id)


def _check(cancel: asyncio.Event) -> None:
    if cancel.is_set():
        raise SyncCancelled


async def _retry(work: Callable[[], Awaitable[Any]], cancel: asyncio.Event, label: str) -> Any:
    """Run one market's step up to ``SCOPE_ATTEMPTS`` times, waiting 2 s then 4 s between tries."""
    for attempt in range(1, SCOPE_ATTEMPTS + 1):
        _check(cancel)
        try:
            return await work()
        except SyncCancelled:
            raise
        except Exception as exc:
            if attempt == SCOPE_ATTEMPTS:
                raise
            log.warning("sync_all.retry", target=label, attempt=attempt, error=_reason(exc))
            await asyncio.sleep(2 * attempt)
    return None


# -- row builders ---------------------------------------------------------
# Tuple order must match the *_COLUMNS constants in db.duck.


def _field_rows(raw: list[dict[str, Any]], target: SyncTarget, now: datetime) -> list[tuple]:
    return [_field_row(DataField.model_validate(item), target, now) for item in raw]


def _field_row(field: DataField, target: SyncTarget, now: datetime) -> tuple:
    return (
        field.id,
        field.dataset.id if field.dataset else None,
        field.category.id if field.category else None,
        field.category.name if field.category else None,
        field.subcategory.id if field.subcategory else None,
        field.subcategory.name if field.subcategory else None,
        field.description,
        field.type,
        field.coverage,
        field.user_count,
        field.alpha_count,
        field.pyramid_multiplier,
        json.dumps(field.themes) if field.themes else None,
        target.instrument_type,
        target.region,
        target.delay,
        target.universe,
        now,
    )


def _dataset_row(dataset: DataSet, target: SyncTarget, now: datetime) -> tuple:
    return (
        dataset.id,
        dataset.name,
        dataset.description,
        dataset.category.id if dataset.category else None,
        dataset.category.name if dataset.category else None,
        dataset.subcategory.id if dataset.subcategory else None,
        dataset.subcategory.name if dataset.subcategory else None,
        dataset.coverage,
        dataset.value_score,
        dataset.user_count,
        dataset.alpha_count,
        dataset.field_count,
        dataset.pyramid_multiplier,
        json.dumps(dataset.themes) if dataset.themes else None,
        target.instrument_type,
        target.region,
        target.delay,
        target.universe,
        now,
    )


def _category_row(
    category: DataCategory, parent_id: str | None, target: SyncTarget, now: datetime
) -> tuple:
    return (
        category.id,
        category.name,
        parent_id,
        category.dataset_count,
        category.field_count,
        category.value_score,
        target.instrument_type,
        target.region,
        target.delay,
        target.universe,
        now,
    )


def _derived_rows(
    raw: list[dict[str, Any]], target: SyncTarget, now: datetime
) -> tuple[list[tuple], list[tuple]]:
    """Dataset and category rows built from the fields alone.

    Every field names its dataset, category and subcategory, so a scope is browsable before
    its details arrive. Read from the raw dicts rather than re-validating ~85k models.
    Stage 2 of a full sync overwrites these with BRAIN's full records.
    """
    datasets: dict[str, dict[str, Any]] = {}
    categories: dict[str, dict[str, Any]] = {}
    for item in raw:
        dataset = item.get("dataset") or {}
        category = item.get("category") or {}
        subcategory = item.get("subcategory") or {}
        dataset_id = dataset.get("id")
        if dataset_id:
            entry = datasets.setdefault(
                dataset_id,
                {"name": dataset.get("name"), "category": category, "sub": subcategory, "n": 0},
            )
            entry["n"] += 1
        for node, parent_id in ((category, None), (subcategory, category.get("id"))):
            node_id = node.get("id")
            if not node_id:
                continue
            c = categories.setdefault(
                node_id, {"name": node.get("name"), "parent": parent_id, "ds": set(), "n": 0}
            )
            c["n"] += 1
            if dataset_id:
                c["ds"].add(dataset_id)

    scope = (target.instrument_type, target.region, target.delay, target.universe)
    dataset_rows = [
        (
            dataset_id,
            e["name"],
            None,
            e["category"].get("id"),
            e["category"].get("name"),
            e["sub"].get("id"),
            e["sub"].get("name"),
            None,
            None,
            None,
            None,
            e["n"],
            None,
            None,
            *scope,
            now,
        )
        for dataset_id, e in datasets.items()
    ]
    category_rows = [
        (node_id, c["name"], c["parent"], len(c["ds"]), c["n"], None, *scope, now)
        for node_id, c in categories.items()
    ]
    return dataset_rows, category_rows


def _reason(exc: BaseException) -> str:
    return getattr(exc, "message", None) or str(exc) or type(exc).__name__


def _failures(progress: dict[str, Any]) -> str | None:
    """Failed scopes as one line for the run's ``error``; None while nothing has failed."""
    failed: list[str] = progress["failed"]
    if not failed:
        return None
    shown = "; ".join(failed[:5])
    more = f"; and {len(failed) - 5} more" if len(failed) > 5 else ""
    return f"{len(failed)} failed: {shown}{more}"


def serialise_run(run: SyncRun) -> dict[str, Any]:
    """Wire shape for the API and the live progress feed."""
    expected = run.fields_expected or 0
    fraction = (run.fields_synced / expected) if expected else None
    everything = run.region == ALL_TARGET.region
    return {
        "id": run.id,
        "instrumentType": run.instrument_type,
        "region": run.region,
        "delay": run.delay,
        "universe": run.universe,
        "all": everything,
        "label": ALL_LABEL
        if everything
        else f"{run.instrument_type}/{run.region}/D{run.delay}/{run.universe}",
        "status": run.status,
        "phase": run.phase,
        "cursorOffset": run.cursor_offset,
        "cursorDataset": run.cursor_dataset,
        "categoriesSynced": run.categories_synced,
        "datasetsSynced": run.datasets_synced,
        "fieldsSynced": run.fields_synced,
        "fieldsExpected": run.fields_expected,
        "fraction": fraction,
        "truncatedDatasets": list(run.truncated_datasets or []),
        "error": run.error,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
    }
