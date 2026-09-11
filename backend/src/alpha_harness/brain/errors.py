"""Typed exceptions for BRAIN API failures.

Business logic should branch on these, never on raw status codes. The API layer maps
them to HTTP responses in exactly one place.
"""

from __future__ import annotations

from typing import Any

DAILY_LIMIT_DETAIL = "DAILY_SIMULATION_LIMIT_EXCEEDED"


class BrainError(RuntimeError):
    """Base for every BRAIN API failure."""

    #: Whether retrying the identical request could plausibly succeed.
    retryable: bool = False

    def __init__(self, message: str, *, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body


class BrainAuthError(BrainError):
    """401 — the session is absent, expired, or the credentials are wrong."""


class BrainVerificationRequired(BrainAuthError):
    """401 carrying an ``inquiry`` — biometric / ID verification is needed.

    Not a credential failure. The user must complete the flow in a browser; ``url`` is
    where to send them.
    """

    def __init__(self, message: str, *, inquiry: str, url: str, body: Any = None) -> None:
        super().__init__(message, status=401, body=body)
        self.inquiry = inquiry
        self.url = url


class BrainForbidden(BrainError):
    """403 — the account lacks permission for this resource or settings combination."""


class BrainNotFound(BrainError):
    """404."""


class BrainValidationError(BrainError):
    """400 — the request was rejected. ``fields`` carries the per-field messages.

    Simulation rejections nest under ``settings`` keyed by the offending field.
    """

    def __init__(self, message: str, *, fields: dict[str, Any], body: Any = None) -> None:
        super().__init__(message, status=400, body=body)
        self.fields = fields


class BrainRateLimited(BrainError):
    """429 — a throttle was hit. Back off and retry."""

    retryable = True

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message, status=429, body=body)
        self.retry_after = retry_after


class BrainDailyLimitReached(BrainRateLimited):
    """429 with ``DAILY_SIMULATION_LIMIT_EXCEEDED``.

    Deliberately **not** retryable: the quota resets on US-Eastern midnight and nothing
    the client does before then will help. Callers must stop, not back off.
    """

    retryable = False


class BrainServiceUnavailable(BrainError):
    """503 — the simulation service is temporarily down. Retryable."""

    retryable = True


class BrainServerError(BrainError):
    """500/502/504 — retryable."""

    retryable = True


class BrainTransportError(BrainError):
    """The request never completed: DNS, TLS, connection reset, timeout."""

    retryable = True


class BrainPollTimeout(BrainError):
    """An asynchronous job kept returning ``Retry-After`` past our ceiling."""
