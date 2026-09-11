"""Typed wrappers over the BRAIN endpoint surface.

One place that knows which endpoint needs which ``Accept`` version, which ones are
asynchronous jobs, and how pagination works. Everything above this layer works with
Pydantic models, never raw dicts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import structlog

from .altcha import Challenge, Solution, solve_async
from .client import BrainClient, BrainResponse
from .errors import BrainDailyLimitReached, BrainRateLimited, BrainVerificationRequired
from .filters import AlphaQuery
from .schemas import (
    Alpha,
    AuthState,
    DataCategory,
    DataSet,
    Operator,
    RecordSet,
    RecordSetRef,
    Simulation,
    SimulationRequest,
)

log = structlog.get_logger(__name__)

# Endpoints pinned to a non-default Accept version. See docs/API.md "Conventions".
# OPTIONS /simulations. Stays on 3.0: 4.0 drops region, universe, neutralization and
# delay from settings.children (checked live 2026-09-10).
V_SETTINGS_SCHEMA = "3.0"
#: GET /data-fields with all four scope params returns the whole scope unpaginated.
V_FIELDS_ALL = "3.0"
V_ALPHA_LIST = "4.0"  # /users/{id}/alphas, /users/self/alphas/summary, /suggest/fields

PAGE_SIZE = 50


class BrainEndpoints:
    """The BRAIN API, typed."""

    def __init__(self, client: BrainClient) -> None:
        self.client = client

    # -- authentication ------------------------------------------------

    async def get_captcha(self) -> Challenge:
        """Fetch an ALTCHA proof-of-work challenge."""
        r = await self.client.request("GET", "/captcha")
        if not isinstance(r.body, dict):
            raise ValueError(f"Unexpected captcha payload: {r.body!r}")
        return Challenge.from_payload(r.body)

    async def solve_captcha(self) -> Solution:
        return await solve_async(await self.get_captcha())

    async def authenticate(
        self, email: str, password: str, *, captcha: str | None = None
    ) -> AuthState:
        """Exchange Basic auth (plus the solved captcha) for a session cookie."""
        body: dict[str, Any] = {}
        if captcha is not None:
            body["captcha"] = captcha
        r = await self.client.request(
            "POST",
            "/authentication",
            json_body=body or None,
            auth=(email, password),
        )
        return AuthState.model_validate(r.body or {})

    async def get_auth(self) -> AuthState | None:
        """Cheapest liveness check. ``None`` means no active session."""
        r = await self.client.request("GET", "/authentication", raise_for_status=False)
        if r.status == 401:
            error = self.client._to_error("GET", "/authentication", r)
            if isinstance(error, BrainVerificationRequired):
                # Not "no session": the session needs a browser check first.
                raise error
            return None
        if r.status == 204:
            return None
        if r.status >= 400 or not isinstance(r.body, dict):
            return None
        return AuthState.model_validate(r.body)

    async def logout(self) -> None:
        await self.client.request("DELETE", "/authentication", raise_for_status=False)
        self.client.clear_cookies()

    async def get_user(self, user_id: str = "self") -> dict[str, Any]:
        """Fetch user profile details from /users/{user_id}."""
        try:
            r = await self.client.request("GET", f"/users/{user_id}", raise_for_status=False)
            if r.status >= 400 or not isinstance(r.body, dict):
                return {}
            return r.body
        except Exception:
            return {}

    # -- platform metadata ----------------------------------------------

    async def settings_schema(self) -> dict[str, Any]:
        """``OPTIONS /simulations`` — the authoritative settings metadata.

        Returns ``actions.POST.settings.children``: every valid region, universe,
        neutralization and delay *and their interdependencies*. Always prefer this over
        hardcoded enums, which drift as the platform adds markets.
        """
        r = await self.client.request("OPTIONS", "/simulations", version=V_SETTINGS_SCHEMA)
        body = r.body if isinstance(r.body, dict) else {}
        children = body.get("actions", {}).get("POST", {}).get("settings", {}).get("children", {})
        return children if isinstance(children, dict) else {}

    async def list_operators(self) -> list[Operator]:
        """Every operator with signature and category — the language reference."""
        r = await self.client.request("GET", "/operators")
        raw = r.body if isinstance(r.body, list) else (r.body or {}).get("results", [])
        return [Operator.model_validate(o) for o in raw]

    async def configuration(self) -> dict[str, Any]:
        r = await self.client.request("GET", "/configuration")
        return r.body if isinstance(r.body, dict) else {}

    # -- simulations -----------------------------------------------------

    async def create_simulation(
        self, payload: SimulationRequest | list[SimulationRequest]
    ) -> BrainResponse:
        """Start a simulation. Returns the raw response — the caller needs ``Location``.

        A ``201`` carries the simulation id **only** in the ``Location`` header; the body
        is empty. For a multi-simulation this is the *parent* id, and it is the only
        handle that can cancel the batch. Persist it before doing anything else.

        Deliberately does not parse the response: :mod:`alpha_harness.engine.tracker`
        owns the ordering that makes cancellation safe.
        """
        if isinstance(payload, list):
            body: Any = [p.to_wire() for p in payload]
        else:
            body = payload.to_wire()
        try:
            return await self.client.request(
                "POST",
                "/simulations",
                json_body=body,
                headers={"Access-Control-Request-Headers": "Location"},
            )
        except BrainRateLimited as exc:
            # A concurrency 429 is requeued by the caller; without a penalty the next
            # engine tick resends it two seconds later into the same throttle.
            if not isinstance(exc, BrainDailyLimitReached):
                self.client.throttle.penalise(exc.retry_after or 10.0)
            raise

    async def get_simulation(self, simulation_id: str) -> Simulation:
        """Read simulation state once, without waiting."""
        r = await self.client.request("GET", f"/simulations/{simulation_id}")
        data = r.body if isinstance(r.body, dict) else {}
        sim = Simulation.model_validate({"id": simulation_id, **data})
        if r.pending and sim.status is None:
            # Still queued: the body carries only progress.
            sim.status = None
        return sim

    async def cancel_simulation(self, simulation_id: str) -> bool:
        """Cancel a queued or running simulation.

        Returns ``False`` if the platform no longer knows the id (already finished or
        cancelled), which is not an error from the caller's point of view.
        """
        r = await self.client.request(
            "DELETE", f"/simulations/{simulation_id}", raise_for_status=False
        )
        return r.status < 400

    # -- alphas ----------------------------------------------------------

    async def get_alpha(self, alpha_id: str) -> Alpha:
        r = await self.client.request("GET", f"/alphas/{alpha_id}")
        return Alpha.model_validate(r.body or {"id": alpha_id})

    async def list_recordsets(self, alpha_id: str) -> list[RecordSetRef]:
        r = await self.client.request("GET", f"/alphas/{alpha_id}/recordsets")
        results = (r.body or {}).get("results", []) if isinstance(r.body, dict) else []
        return [RecordSetRef.model_validate(x) for x in results]

    async def get_recordset(
        self, alpha_id: str, name: str, *, timeout: float | None = None
    ) -> RecordSet:
        """Fetch a time series. Asynchronous — goes through the Retry-After protocol."""
        r = await self.client.poll("GET", f"/alphas/{alpha_id}/recordsets/{name}", timeout=timeout)
        return RecordSet.model_validate(r.body or {})

    async def check_alpha(self, alpha_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        """Re-run submission checks without submitting. Asynchronous."""
        r = await self.client.poll("GET", f"/alphas/{alpha_id}/check", timeout=timeout)
        return r.body if isinstance(r.body, dict) else {}

    async def correlations(
        self, alpha_id: str, kind: str = "self", *, timeout: float | None = None
    ) -> dict[str, Any]:
        """``self`` or ``prod`` correlation. Asynchronous, and rate limited per hour."""
        r = await self.client.poll(
            "GET", f"/alphas/{alpha_id}/correlations/{kind}", timeout=timeout
        )
        return r.body if isinstance(r.body, dict) else {}

    # -- the alpha pool --------------------------------------------------
    #
    # Listing needs ``version=4.0`` and the filter DSL, where the comparison operator is
    # part of the parameter name. The query is therefore built by
    # :mod:`alpha_harness.brain.filters` and appended to the path, never passed as a
    # params dict — a dict helper encodes the operator into the value and the server
    # then matches nothing.
    #
    # There is deliberately no ``submit`` here. Submission is irreversible, and the
    # absence of the method is the guarantee: no code path can reach it by mistake.

    async def list_alphas(self, query: AlphaQuery, user_id: str = "self") -> dict[str, Any]:
        """One page of your alphas, with the total match count."""
        r = await self.client.request("GET", query.path(user_id), version=V_ALPHA_LIST)
        body = r.body if isinstance(r.body, dict) else {}
        return {
            "count": int(body.get("count") or 0),
            "results": body.get("results") or [],
            "limit": query.limit,
            "offset": query.offset,
        }

    async def alphas_summary(self) -> dict[str, Any]:
        """Aggregate counts by stage and status."""
        r = await self.client.request("GET", "/users/self/alphas/summary", version=V_ALPHA_LIST)
        return r.body if isinstance(r.body, dict) else {}

    async def alpha_filter_schema(self, user_id: str = "self") -> dict[str, Any]:
        """Which fields can be filtered and sorted, from the platform itself.

        Read rather than hardcoded, for the same reason as the simulation settings: the
        list grows, and a stale copy silently drops filters the platform supports.
        """
        r = await self.client.request("OPTIONS", f"/users/{user_id}/alphas", version=V_ALPHA_LIST)
        return r.body if isinstance(r.body, dict) else {}

    async def similar_alphas(self, alpha_id: str, limit: int = 5) -> list[dict[str, Any]]:
        r = await self.client.request(
            "GET", f"/alphas/{alpha_id}/alphas?limit={limit}", version=V_ALPHA_LIST
        )
        body = r.body if isinstance(r.body, dict) else {}
        return body.get("results") or []

    async def patch_alpha(self, alpha_id: str, changes: dict[str, Any]) -> Alpha:
        """Edit one alpha: name, colour, tags, favourite, hidden.

        Single alpha only. ``PATCH /alphas`` edits several at once and is deliberately
        not wrapped — a bulk write driven by a filter is exactly the kind of thing that
        is easy to fire and impossible to undo.
        """
        r = await self.client.request("PATCH", f"/alphas/{alpha_id}", json_body=changes)
        return Alpha.model_validate(r.body or {"id": alpha_id})

    # -- tags ------------------------------------------------------------

    async def list_tags(self) -> list[dict[str, Any]]:
        r = await self.client.request("GET", "/tags")
        body = r.body if isinstance(r.body, dict) else {}
        return body.get("results") or []

    async def tag_correlations(
        self, tag_id: str, *, timeout: float | None = None
    ) -> dict[str, Any]:
        """Cross-correlation within a tagged set. Asynchronous."""
        r = await self.client.poll("GET", f"/tags/{tag_id}/correlations/inner", timeout=timeout)
        return r.body if isinstance(r.body, dict) else {}

    # -- data catalog ----------------------------------------------------

    async def list_data_categories(self, **params: Any) -> list[DataCategory]:
        r = await self.client.request_retrying("GET", "/data-categories", params=params)
        raw = r.body if isinstance(r.body, list) else (r.body or {}).get("results", [])
        return [DataCategory.model_validate(c) for c in raw]

    async def list_data_fields_all(self, **params: Any) -> list[dict[str, Any]]:
        """Every field in one scope, in one unpaginated request.

        Needs all four scope parameters and ``version=3.0``; otherwise the endpoint is
        the paged one, which refuses offsets at or beyond 10,000.
        """
        r = await self.client.request_retrying(
            "GET", "/data-fields", version=V_FIELDS_ALL, params=params
        )
        body = r.body
        rows = (
            body
            if isinstance(body, list)
            else (body.get("results") if isinstance(body, dict) else None)
        )
        return rows if isinstance(rows, list) else []

    async def pyramid_multipliers(self) -> list[dict[str, Any]]:
        """``{category, region, delay, multiplier}`` for every pyramid on this account."""
        r = await self.client.request_retrying("GET", "/users/self/activities/pyramid-multipliers")
        items = r.body.get("pyramids") if isinstance(r.body, dict) else None
        return items if isinstance(items, list) else []

    async def pyramid_alphas(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """``{category, region, delay, alphaCount}`` submitted between two ISO dates."""
        r = await self.client.request_retrying(
            "GET",
            "/users/self/activities/pyramid-alphas",
            params={"startDate": start_date, "endDate": end_date},
        )
        items = r.body.get("pyramids") if isinstance(r.body, dict) else None
        return items if isinstance(items, list) else []

    async def activity_counts(self, name: str, since: str) -> list[tuple[str, int]]:
        """``(date, count)`` per platform day from an ISO date on.

        ``name`` is ``simulations`` or ``submissions``. The series sits under a doubled
        ``records.records`` key; rows that are not ``[date, number]`` are dropped.

        The documented ``?date>=2024-03-19`` is the field ``date>`` and then ``=``. A
        ``date>=`` key is answered with 422 "Expected date" (checked live 2026-09-10).
        """
        r = await self.client.request_retrying(
            "GET", f"/users/self/activities/{name}", params={"date>": since}
        )
        outer = r.body.get("records") if isinstance(r.body, dict) else None
        rows = outer.get("records") if isinstance(outer, dict) else None
        return [
            (str(row[0]), int(row[1]))
            for row in rows or []
            if isinstance(row, list | tuple) and len(row) >= 2 and isinstance(row[1], int | float)
        ]

    async def iter_data_sets(self, **params: Any) -> AsyncIterator[DataSet]:
        async for item in self._paginate("/data-sets", params):
            yield DataSet.model_validate(item)

    async def iter_data_fields(self, **params: Any) -> AsyncIterator[dict[str, Any]]:
        """Page ``/data-fields`` in its browse form (no version pin), raw rows.

        The platform refuses offsets at or beyond 10,000, so a large scope is paged one
        dataset at a time by passing ``dataset.id``. Needed for scopes the unpaginated form
        answers with nothing: the region-agnostic ``ALL`` region (checked live 2026-09-11 —
        140 datasets, ``count`` capped at 10,000 for the whole scope).
        """
        async for item in self._paginate("/data-fields", params):
            yield item

    async def _paginate(
        self, path: str, params: dict[str, Any], *, page_size: int = PAGE_SIZE
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk a DRF ``limit``/``offset`` list endpoint.

        Stops when a page comes back short or when ``count`` is reached, and guards
        against a server that keeps returning full pages forever.
        """
        offset = 0
        seen = 0
        total: int | None = None

        while True:
            # Retrying: long crawls meet the platform's throttle, and abandoning a
            # partially-walked list would silently under-report the catalog.
            r = await self.client.request_retrying(
                "GET", path, params={**params, "limit": page_size, "offset": offset}
            )
            body = r.body if isinstance(r.body, dict) else {}
            results = body.get("results") or []
            if total is None:
                total = body.get("count")

            for item in results:
                yield item
            seen += len(results)

            if len(results) < page_size:
                return
            if total is not None and seen >= total:
                return
            offset += page_size
