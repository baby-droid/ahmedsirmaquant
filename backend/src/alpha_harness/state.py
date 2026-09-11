"""Composition root.

One object owns every long-lived resource — both databases, the HTTP client, the
tracker, the sync engine — and wires them together. Routers reach it through a FastAPI
dependency, so nothing constructs its own connections and shutdown is deterministic.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import structlog

from .brain.client import BrainClient
from .brain.endpoints import BrainEndpoints
from .catalog.queries import CatalogQueries
from .catalog.sync import CatalogSync
from .config import Settings, get_settings
from .db.duck import Catalog
from .db.sqlite import Database
from .engine.slots import BatchEngine
from .engine.tracker import SimulationTracker
from .harvest.seeds import SeedHarvester
from .labs.diversify import Diversifier
from .labs.harden import Hardener
from .labs.invent import Inventor
from .labs.pair import Pairer
from .labs.registry import LabRegistry
from .labs.relocate import Relocator
from .labs.repair import Repairer
from .llm.chat import ChatService
from .llm.registry import ModelRegistry
from .llm.service import LLMService
from .optimize.study import Optimizer
from .plan.advisor import PlanAdvisor
from .plan.day import DayPlanner
from .plan.dispatch import Dispatcher
from .plan.pm import PortfolioManager
from .plan.skeletons import SkeletonBook
from .plan.yields import YieldBook
from .realtime import (
    TOPIC_SESSION,
    TOPIC_SIMULATIONS,
    TOPIC_STUDIES,
    TOPIC_SYNC,
    TOPIC_TASKS,
    Hub,
)
from .security.vault import Vault
from .services.auth import AuthService
from .tasks import TaskRegistry
from .templates.library import TemplateLibrary
from .templates.studio import Studio
from .vault.backfill import Backfill
from .vault.mixing import Mixer
from .vault.store import AlphaVault

log = structlog.get_logger(__name__)


class AppState:
    """Everything the application needs, constructed once."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_data_dir()

        self.hub = Hub()
        self.tasks = TaskRegistry(
            on_change=lambda payload: self.hub.broadcast(TOPIC_TASKS, payload)
        )
        self.vault = Vault.from_path(self.settings.key_path)
        self.db = Database.for_path(self.settings.sqlite_path)
        self.catalog = Catalog(self.settings.duckdb_path)

        self.client = BrainClient(
            self.settings.brain_api_base,
            min_retry_after=self.settings.min_retry_after_seconds,
            poll_timeout=self.settings.poll_timeout_seconds,
            min_request_interval=self.settings.min_request_interval_seconds,
            default_attempts=self.settings.request_attempts,
        )
        self.endpoints = BrainEndpoints(self.client)
        self.auth = AuthService(self.db, self.vault, self.endpoints)

        self.tracker = SimulationTracker(
            self.db,
            self.endpoints,
            on_change=lambda payload: self.hub.broadcast(TOPIC_SIMULATIONS, payload),
        )
        self.engine = BatchEngine(
            self.db,
            self.endpoints,
            self.tracker,
            on_change=lambda payload: self.hub.broadcast(TOPIC_SIMULATIONS, payload),
        )
        self.sync = CatalogSync(
            self.db,
            self.catalog,
            self.endpoints,
            on_progress=lambda payload: self.hub.broadcast(TOPIC_SYNC, payload),
        )
        self.queries = CatalogQueries(self.catalog)

        # Every alpha ever simulated, its daily returns, and what can be learned from
        # correlating them. The tracker feeds this as simulations complete.
        self.alphas = AlphaVault(self.catalog)
        self.mixer = Mixer(self.catalog)
        self.backfill = Backfill(
            self.alphas,
            self.endpoints,
            self.tasks,
            # A resolved check can turn an alpha submittable minutes after its
            # simulation finished, so the screen listing them has to hear about it.
            on_checked=lambda payload: self.hub.broadcast(TOPIC_SIMULATIONS, payload),
        )
        # Which expression *shapes* this market has already answered. Two labs can
        # search different axes and still fill the book with one shape, and the platform
        # judges the shape — so the labs draw from a menu that narrows as it is used.
        self.skeletons = SkeletonBook(self.db, self.catalog)
        self.harvester = SeedHarvester(self.queries, self.skeletons)
        self.tracker.on_alpha = self.backfill.capture

        # A day's allowance, split across a few different lines of research. Owns no
        # loop of its own: it shapes the queue and the engine drains it.
        self.planner = DayPlanner(self.db, self.engine, self.harvester)

        # The same formula, pointed at another market. Every destination is checked
        # field-by-field first: simulating a field a market does not have spends the
        # allowance to learn nothing.
        self.relocator = Relocator(self.queries, self.alphas)

        # Two fields at once: what one says relative to another. A different
        # axis from Sweep, and where most of the shapes that keep working live.
        self.pairer = Pairer(self.queries, self.harvester, self.skeletons, self.auth)

        # The widest search there is: the shape of the formula itself, built as a typed
        # tree and bred from whatever has already worked in this market. Needs no key.
        self.inventor = Inventor(
            self.queries, self.harvester, self.alphas, self.skeletons, self.auth
        )

        # An alpha that failed one check is a finished piece of research with one thing
        # wrong. This changes that one thing and nothing else, so the platform's next
        # verdict says whether the fix worked.
        self.repairer = Repairer(self.alphas, self.skeletons, self.mixer, self.auth)

        # The counterweight to tuning. Deepen climbs towards a peak; this checks the
        # peak is a plateau rather than a spike, which is what keeps yield honest.
        self.hardener = Hardener(self.queries)

        # Distance rather than score: an alpha too close to the pool cannot be
        # submitted however good it is, and the rejection arrives after the spend.
        self.diversifier = Diversifier(self.queries, self.harvester)

        self.templates = TemplateLibrary(self.db)
        self.studio = Studio(self.queries, self.auth)
        self.models = ModelRegistry()
        self.llm = LLMService(self.db, self.vault, self.queries, self.models)
        self.chat = ChatService(self.db, self.llm, self.queries)
        # Lets the assistant choose the day's research — within the same option space a
        # person picks from, so its worst case is an ordinary random plan.
        self.advisor = PlanAdvisor(self.planner, self.llm)

        # The fund's books, and the manager who reads them. Yield is measured from what
        # actually ran; the PM turns that into an allocation of cores across desks.
        self.labs = LabRegistry(self)
        self.yields = YieldBook(self.db, self.catalog)
        self.pm = PortfolioManager(self.labs, self.yields, self.llm)
        # Turns the PM's allocation into queued work. Owns no loop: the engine drains
        # what it queues, across the cores each desk was given.
        self.dispatcher = Dispatcher(self)
        self.optimizer = Optimizer(
            self.db,
            self.engine,
            self.endpoints,
            self.studio,
            on_change=lambda payload: self.hub.broadcast(TOPIC_STUDIES, payload),
            alphas=self.alphas,
            backfill=self.backfill,
        )
        # LLM Power Pool Lab tasks call the assistant while they run.
        self.optimizer.llm = self.llm  # type: ignore[attr-defined]

    # -- lifecycle -------------------------------------------------------

    async def startup(self) -> None:
        await self.db.create_all()
        await self.catalog.open()

        # Reuse a cached session before anything else; a restart should not cost a
        # proof-of-work solve or count against the sign-in lockout budget.
        try:
            session = await self.auth.restore()
            if session.authenticated:
                log.info("startup.session_restored", user_id=session.user_id)
                # Batching needs MULTI_SIMULATION; without it every batch is one run.
                self.engine.configure_from_permissions(session.permissions)
        except Exception:
            log.warning("startup.session_restore_failed", exc_info=True)

        # Adopt anything that was in flight when we last stopped.
        try:
            result = await self.tracker.reconcile()
            if result["orphaned"]:
                log.warning("startup.orphaned_simulations", count=result["orphaned"])
        except Exception:
            log.warning("startup.reconcile_failed", exc_info=True)

        # An empty Template Studio is a blank page for someone who has never written a
        # Fast Expression. Seeded once, then never again.
        try:
            await self.templates.seed()
        except Exception:
            log.warning("startup.template_seed_failed", exc_info=True)

        # A sync killed with the process would otherwise report RUNNING forever.
        try:
            await self.sync.reset_interrupted()
        except Exception:
            log.warning("startup.sync_reset_failed", exc_info=True)

        await self.tracker.start()
        await self.engine.start()
        await self.optimizer.start()
        self._session_watch = asyncio.create_task(self._watch_session(), name="session-watch")
        log.info("startup.complete", data_dir=str(self.settings.data_dir))

    async def _watch_session(self) -> None:
        """Renew the BRAIN session shortly before it expires, so running work keeps going."""
        last_attempt = float("-inf")
        while True:
            await asyncio.sleep(60)
            session = self.auth.session
            left = session.expires_in_seconds
            if not session.authenticated or left is None or left > 600:
                continue
            # ponytail: one silent re-login per hour at most; a failed sign-in spends
            # BRAIN's lockout budget. Raise only if sessions shorter than an hour appear.
            if time.monotonic() - last_attempt < 3600:
                continue
            last_attempt = time.monotonic()
            try:
                if await self.auth.get_credential() is None:
                    continue
                info = await self.auth.login()
                if info.authenticated:
                    self.engine.configure_from_permissions(info.permissions)
                await self.hub.broadcast(TOPIC_SESSION, info.to_dict())
            except Exception:
                log.warning("session.renew_failed", exc_info=True)

    async def shutdown(self) -> None:
        watch = getattr(self, "_session_watch", None)
        if watch is not None:
            watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watch
        await self.optimizer.stop()
        await self.engine.stop()
        await self.tracker.stop()
        await self.sync.shutdown()
        await self.client.aclose()
        await self.catalog.close()
        await self.db.dispose()
        log.info("shutdown.complete")
