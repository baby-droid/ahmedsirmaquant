"""Encryption of secrets at rest.

Secrets (the BRAIN password, Google API keys) are sealed with AES-256-GCM and stored as
opaque blobs in SQLite. The key lives in a single file created ``0600`` under the data
directory.

Threat model, stated plainly: this protects against casual disclosure — a stray backup,
an accidental `git add`, someone reading the database file. It does **not** protect
against an attacker who already has your user account, because the key is readable by
that same account. That is the deliberate trade for a local-first tool that must resume
polling simulations unattended after a restart without prompting for a passphrase.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # GCM standard


class VaultError(RuntimeError):
    """Raised when a secret cannot be sealed or opened."""


def load_or_create_key(path: Path) -> bytes:
    """Return the master key, creating it 0600 on first use.

    Uses ``O_EXCL`` so two processes racing on first start cannot clobber each other's
    key — the loser falls back to reading what the winner wrote.
    """
    if path.exists():
        key = path.read_bytes()
        if len(key) != KEY_BYTES:
            raise VaultError(
                f"Key at {path} is {len(key)} bytes, expected {KEY_BYTES}. "
                "Refusing to guess. Move it aside to generate a fresh one — "
                "stored secrets will need to be re-entered."
            )
        _warn_if_permissive(path)
        return key

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def _warn_if_permissive(path: Path) -> None:
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        # Tighten rather than merely complain; the user cannot act on a log line.
        path.chmod(0o600)


class Vault:
    """Seals and opens secrets with a single AES-GCM key."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise VaultError(f"Key must be {KEY_BYTES} bytes, got {len(key)}")
        self._aead = AESGCM(key)

    @classmethod
    def from_path(cls, path: Path) -> Vault:
        return cls(load_or_create_key(path))

    def seal(self, plaintext: str, *, context: str = "") -> bytes:
        """Encrypt ``plaintext``.

        ``context`` is bound as additional authenticated data, so a blob sealed for one
        purpose cannot be silently substituted into another.
        """
        nonce = secrets.token_bytes(NONCE_BYTES)
        ct = self._aead.encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
        return nonce + ct

    def open(self, blob: bytes, *, context: str = "") -> str:
        """Decrypt a blob produced by :meth:`seal`."""
        if len(blob) <= NONCE_BYTES:
            raise VaultError("Ciphertext is too short to contain a nonce")
        nonce, ct = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
        try:
            return self._aead.decrypt(nonce, ct, context.encode("utf-8")).decode("utf-8")
        except InvalidTag as exc:
            raise VaultError(
                "Could not decrypt secret: wrong key, wrong context, or corrupted data"
            ) from exc
