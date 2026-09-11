"""Credential storage and session lifecycle.

Ties three things together: the vault (sealing secrets), SQLite (persisting them), and
the BRAIN authenticator (using them).

The session cookie jar is persisted deliberately. Signing in costs a proof-of-work solve
and counts against a lockout budget, so a backend restart must not trigger a new one.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select

from ..brain.auth import Authenticator, SessionInfo
from ..brain.endpoints import BrainEndpoints
from ..db.models import BrainSessionRow, Credential, MetadataCache, utcnow
from ..db.sqlite import Database
from ..security.vault import Vault

log = structlog.get_logger(__name__)

PASSWORD_CONTEXT = "brain-password"
COOKIE_CONTEXT = "brain-cookies"


class NoCredentialError(RuntimeError):
    """No BRAIN credential has been stored and none was supplied."""


class AuthService:
    """The app's view of "are we signed in to BRAIN?"."""

    def __init__(
        self,
        db: Database,
        vault: Vault,
        endpoints: BrainEndpoints,
        *,
        authenticator: Authenticator | None = None,
    ) -> None:
        self.db = db
        self.vault = vault
        self.endpoints = endpoints
        self.auth = authenticator or Authenticator(endpoints)
        self._session = SessionInfo.anonymous()
        self._user_profile: dict[str, Any] | None = None

    @property
    def session(self) -> SessionInfo:
        """Last known session state. Cheap; does not hit the network."""
        return self._session

    async def get_user_profile(self) -> dict[str, Any]:
        """Cached user profile from BRAIN /users/{userId}."""
        if not self._session.authenticated or not self._session.user_id:
            return {}
        if self._user_profile is not None:
            return self._user_profile
        try:
            profile = await self.endpoints.get_user(self._session.user_id)
            if profile:
                self._user_profile = profile
                first = str(profile.get("firstName") or "").strip()
                last = str(profile.get("lastName") or "").strip()
                full_name = (
                    profile.get("fullName")
                    or profile.get("name")
                    or (f"{first} {last}".strip() or None)
                )
                if full_name:
                    self._session.full_name = full_name
            return profile
        except Exception as exc:
            log.warning("brain.user_profile.failed", error=str(exc))
            return {}

    # -- credential storage ----------------------------------------------

    async def store_credential(self, email: str, password: str) -> None:
        """Save (or replace) the BRAIN login, sealed at rest."""
        sealed = self.vault.seal(password, context=PASSWORD_CONTEXT)
        async with self.db.session() as session:
            existing = (
                (await session.execute(select(Credential).where(Credential.email == email)))
                .scalars()
                .first()
            )
            if existing is not None:
                existing.password_sealed = sealed
            else:
                session.add(Credential(email=email, password_sealed=sealed))
        log.info("credential.stored", email=_mask(email))

    async def get_credential(self) -> tuple[str, str] | None:
        """Return the stored ``(email, password)``, unsealed."""
        async with self.db.session() as session:
            credential = await _current_credential(session)
            if credential is None:
                return None
            password = self.vault.open(credential.password_sealed, context=PASSWORD_CONTEXT)
            return credential.email, password

    async def stored_email(self) -> str | None:
        async with self.db.session() as session:
            credential = await _current_credential(session)
            return credential.email if credential else None

    async def forget(self) -> None:
        """Remove the credential and any cached session."""
        async with self.db.session() as session:
            for credential in (await session.execute(select(Credential))).scalars():
                await session.delete(credential)
        self.endpoints.client.clear_cookies()
        self._session = SessionInfo.anonymous()

    # -- session ---------------------------------------------------------

    async def restore(self) -> SessionInfo:
        """Reuse a cached cookie jar if it is still valid. Called at startup."""
        cookies = await self._load_cookies()
        restored = await self.auth.restore(cookies)
        if restored is None or not restored.authenticated:
            # Unauthenticated but restorable (identity verification pending) keeps its
            # verification link; its cookies are left where they are.
            self._session = restored or SessionInfo.anonymous()
            return self._session
        self._session = restored
        await self._save_cookies(restored)
        await self._warm_operators()
        return restored

    async def login(self, email: str | None = None, password: str | None = None) -> SessionInfo:
        """Sign in, storing the credential if it was supplied here and accepted."""
        supplied = bool(email and password)
        if not supplied:
            credential = await self.get_credential()
            if credential is None:
                raise NoCredentialError(
                    "No BRAIN credentials stored. Sign in with your BRAIN email and "
                    "password; they are sealed on this machine and never leave it."
                )
            email, password = credential
        assert email is not None and password is not None

        self._user_profile = None
        # A pending verification is finished on its own inquiry, never by a new sign-in.
        pending = self._session.verification_url
        info = await self.auth.verify(pending, email, password) if pending else None
        if info is None:
            info = await self.auth.login(email, password)
        self._session = info
        if info.authenticated:
            # Stored only once BRAIN accepts it: a mistyped password must not become the
            # credential that silent re-login keeps retrying.
            if supplied:
                await self.store_credential(email, password)
            await self._touch_last_login(email)
            await self._save_cookies(info)
            await self._touch_last_login(email)
            # Warm the profile so the first screen can greet them by name without a
            # second round trip. Failure is swallowed inside; a missing name is a
            # cosmetic loss, never a reason to fail a successful sign-in.
            await self.get_user_profile()
            await self._warm_operators()
        return info

    async def ensure(self) -> SessionInfo:
        """Restore if possible, sign in only if needed."""
        if self._session.authenticated:
            return self._session
        restored = await self.restore()
        if restored.authenticated:
            return restored
        return await self.login()

    async def status(self, *, refresh: bool = False) -> SessionInfo:
        """Current state. ``refresh`` re-validates against the platform."""
        if not refresh:
            return self._session
        fresh = await self.auth.status()
        # The profile is cached, so re-reading it returns early and would not set the
        # name again on the new object.
        if fresh.authenticated and not fresh.full_name:
            fresh.full_name = self._session.full_name
        self._session = fresh
        if self._session.authenticated:
            await self.get_user_profile()
        return self._session

    async def logout(self) -> None:
        self._user_profile = None
        await self.auth.logout()
        await self._clear_cookies()
        self._session = SessionInfo.anonymous()

    # -- cookie persistence ----------------------------------------------

    async def _load_cookies(self) -> list[dict[str, Any]] | None:
        import json

        async with self.db.session() as session:
            row = (
                (
                    await session.execute(
                        select(BrainSessionRow).order_by(BrainSessionRow.updated_at.desc()).limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            try:
                return json.loads(self.vault.open(row.cookies_sealed, context=COOKIE_CONTEXT))
            except Exception:
                log.warning("session.cookies_unreadable")
                return None

    async def _save_cookies(self, info: SessionInfo) -> None:
        import json
        from datetime import UTC, datetime

        cookies = self.endpoints.client.export_cookies()
        if not cookies:
            return
        sealed = self.vault.seal(json.dumps(cookies), context=COOKIE_CONTEXT)
        expires = datetime.fromtimestamp(info.expires_at, tz=UTC) if info.expires_at else None

        async with self.db.session() as session:
            credential = await _current_credential(session)
            if credential is None:
                return
            row = (
                (
                    await session.execute(
                        select(BrainSessionRow).where(
                            BrainSessionRow.credential_id == credential.id
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                session.add(
                    BrainSessionRow(
                        credential_id=credential.id,
                        cookies_sealed=sealed,
                        user_id=info.user_id,
                        permissions=info.permissions,
                        expires_at=expires,
                    )
                )
            else:
                row.cookies_sealed = sealed
                row.user_id = info.user_id
                row.permissions = info.permissions
                row.expires_at = expires

    async def _clear_cookies(self) -> None:
        async with self.db.session() as session:
            for row in (await session.execute(select(BrainSessionRow))).scalars():
                await session.delete(row)

    async def _touch_last_login(self, email: str) -> None:
        async with self.db.session() as session:
            credential = (
                (await session.execute(select(Credential).where(Credential.email == email)))
                .scalars()
                .first()
            )
            if credential is not None:
                credential.last_login_at = utcnow()

    # -- platform metadata -----------------------------------------------

    async def refresh_metadata(self) -> dict[str, Any]:
        """Cache ``OPTIONS /simulations``.

        The authoritative region / universe / neutralization values and their
        interdependencies. Refreshed on login rather than hardcoded, because the set
        changes as the platform adds markets and as the account's permissions change.
        """
        schema = await self.endpoints.settings_schema()
        await self._cache("settings_schema", schema)
        return schema

    async def cached_settings_schema(self) -> dict[str, Any] | None:
        return await self._read_cache("settings_schema")

    async def refresh_operators(self) -> list[dict[str, Any]]:
        operators = await self.endpoints.list_operators()
        payload = [o.model_dump(by_alias=True) for o in operators]
        await self._cache("operators", {"items": payload})
        return payload

    async def _warm_operators(self) -> None:
        """Cache the account's own operator list once signed in.

        Labs and validation read it, and it was never fetched before. A failure is logged,
        never raised: signing in must not fail because this list could not be read.
        """
        try:
            await self.refresh_operators()
        except Exception:
            log.warning("operators.refresh_failed", exc_info=True)

    async def cached_operators(self) -> list[dict[str, Any]] | None:
        cached = await self._read_cache("operators")
        return cached.get("items") if cached else None

    async def _cache(self, key: str, value: dict[str, Any]) -> None:
        async with self.db.session() as session:
            row = await session.get(MetadataCache, key)
            if row is None:
                session.add(MetadataCache(key=key, value=value))
            else:
                row.value = value
                row.fetched_at = utcnow()

    async def _read_cache(self, key: str) -> dict[str, Any] | None:
        async with self.db.session() as session:
            row = await session.get(MetadataCache, key)
            return row.value if row else None


async def _current_credential(session: Any) -> Credential | None:
    """The credential in use: the one that signed in most recently.

    Oldest-first meant signing in with a different email stored a second row that
    silent re-login and the cookie jar never used.
    """
    return (
        (
            await session.execute(
                select(Credential)
                .order_by(Credential.last_login_at.desc().nulls_last(), Credential.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


def _mask(email: str) -> str:
    """Never log a full address."""
    name, _, domain = email.partition("@")
    head = name[:2] if len(name) > 2 else name[:1]
    return f"{head}***@{domain}" if domain else f"{head}***"
