"""WebSocket fan-out for live telemetry.

One multiplexed channel carries every live update — simulation status, elapsed time,
catalog sync progress — so the UI opens a single socket rather than polling several
endpoints. Commands still go over REST; this is strictly server to client.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import structlog
from fastapi import WebSocket

log = structlog.get_logger(__name__)


class Hub:
    """Tracks connected clients and broadcasts messages to them.

    No lock: the set is only ever mutated by a single statement with no ``await`` in it,
    which the event loop cannot interleave. The sends happen against a copy, outside any
    critical section, because those genuinely do yield.
    """

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        #: Last message per topic, replayed to a client on connect so a freshly opened
        #: tab shows current state immediately instead of waiting for the next change.
        self._latest: dict[str, dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        for message in list(self._latest.values()):
            with contextlib.suppress(Exception):
                await websocket.send_text(json.dumps(message))
        log.debug("hub.connected", clients=len(self._clients))

    async def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)
        log.debug("hub.disconnected", clients=len(self._clients))

    async def broadcast(self, topic: str, payload: Any) -> None:
        """Send to every client, dropping any that have gone away."""
        message = {"topic": topic, "payload": payload}
        self._latest[topic] = message
        text = json.dumps(message, default=str)

        clients = list(self._clients)
        if not clients:
            return

        dead: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_text(text)
            except Exception:
                dead.append(client)

        for client in dead:
            self._clients.discard(client)

    @property
    def client_count(self) -> int:
        return len(self._clients)


#: Topic names shared with the frontend. Keep in sync with frontend/src/lib/realtime.ts.
TOPIC_SIMULATIONS = "simulations"
TOPIC_SYNC = "sync"
TOPIC_SESSION = "session"
TOPIC_STUDIES = "studies"
TOPIC_TASKS = "tasks"
