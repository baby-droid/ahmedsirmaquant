"""Sign-in, session state, and cached platform metadata."""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..brain.settings_schema import resolve_options, validate_settings
from ..realtime import TOPIC_SESSION
from .deps import State

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Omit both fields to sign in with the stored credential."""

    email: str | None = Field(default=None, description="Leave empty to use the saved login")
    password: str | None = Field(default=None, repr=False)


@router.get("/status")
async def status(
    state: State,
    refresh: bool = Query(
        False, description="Re-validate against BRAIN instead of returning cached state"
    ),
) -> dict[str, Any]:
    """Current session. The whole UI keys off this."""
    info = await state.auth.status(refresh=refresh)
    return {
        **info.to_dict(),
        "storedEmail": await state.auth.stored_email(),
    }


@router.post("/login")
async def login(payload: LoginRequest, state: State) -> dict[str, Any]:
    """Sign in to BRAIN.

    Solves the ALTCHA proof-of-work, exchanges Basic auth for a session cookie, then
    caches the cookie jar so a restart does not repeat the work.
    """
    info = await state.auth.login(payload.email, payload.password)

    if info.authenticated:
        # Batch size follows MULTI_SIMULATION. Startup applies it when it restores a
        # session; a fresh sign-in has to as well.
        state.engine.configure_from_permissions(info.permissions)
        # Refresh the authoritative settings schema now that permissions are known —
        # available regions and universes depend on the account. Never block sign-in
        # on it; the cached schema is good enough if this fails.
        with contextlib.suppress(Exception):
            await state.auth.refresh_metadata()

    await state.hub.broadcast(TOPIC_SESSION, info.to_dict())
    return info.to_dict()


@router.post("/logout")
async def logout(state: State) -> dict[str, Any]:
    await state.auth.logout()
    info = state.auth.session
    await state.hub.broadcast(TOPIC_SESSION, info.to_dict())
    return info.to_dict()


@router.delete("/credential")
async def forget_credential(state: State) -> dict[str, bool]:
    """Erase the stored login and cached session from local storage."""
    await state.auth.forget()
    await state.hub.broadcast(TOPIC_SESSION, state.auth.session.to_dict())
    return {"ok": True}


@router.get("/settings-schema")
async def settings_schema(
    state: State,
    refresh: bool = Query(False, description="Re-fetch from BRAIN"),
) -> dict[str, Any]:
    """Valid simulation settings, from ``OPTIONS /simulations``.

    Drives every settings dropdown in the UI. Served from cache unless refreshed, so the
    form still renders when offline.
    """
    if refresh:
        return {"cached": False, "schema": await state.auth.refresh_metadata()}
    cached = await state.auth.cached_settings_schema()
    if cached is None:
        return {"cached": False, "schema": await state.auth.refresh_metadata()}
    return {"cached": True, "schema": cached}


class ResolveOptionsRequest(BaseModel):
    """Settings chosen so far. Partial is fine — that is the point."""

    settings: dict[str, Any] = Field(default_factory=dict)


@router.post("/settings-options")
async def settings_options(payload: ResolveOptionsRequest, state: State) -> dict[str, Any]:
    """Valid options for every settings field, given what is already chosen.

    ``OPTIONS /simulations`` returns a *recursive* structure: the legal universes depend
    on the region, which depends on the instrument type. Resolving it here means the
    settings form can only ever offer combinations the platform accepts — CHN shows
    ``TOP2000U`` and nothing else.
    """
    schema = await state.auth.cached_settings_schema()
    if schema is None:
        schema = await state.auth.refresh_metadata()

    return {
        "fields": resolve_options(schema, payload.settings),
        # Only values actually chosen are judged — a field the user has not reached yet
        # is not an error while the form is being filled in.
        "problems": validate_settings(schema, payload.settings),
        "missing": validate_settings(schema, payload.settings, require_all=True),
    }


@router.get("/operators")
async def operators(
    state: State,
    refresh: bool = Query(False, description="Re-fetch from BRAIN"),
) -> dict[str, Any]:
    """The Fast Expression operator reference, for autocomplete and validation."""
    if not refresh:
        cached = await state.auth.cached_operators()
        if cached is not None:
            return {"cached": True, "count": len(cached), "operators": cached}
    fresh = await state.auth.refresh_operators()
    return {"cached": False, "count": len(fresh), "operators": fresh}
