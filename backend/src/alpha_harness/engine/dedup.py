"""Canonical hashing of simulation payloads.

The daily simulation cap is the binding constraint on research throughput, and
re-running an identical alpha consumes quota for nothing — the platform counts it even
though the alpha already exists. Hashing the full request (settings *and* expression)
lets us recognise a repeat and reuse the previous alpha id instead.

See ``docs/worldquantbrain/brain-api/how-can-you-avoid-duplicate-simulations.md``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """Stable JSON: sorted keys, no incidental whitespace.

    Two requests that differ only in key order or float formatting must hash the same,
    or the cache silently misses and the quota is spent anyway.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def hash_payload(payload: Any) -> str:
    """SHA-256 of the canonical form."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
