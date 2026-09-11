"""The live telemetry socket.

One connection carries every topic. Server-to-client only — commands go over REST — so
the client needs no request/response correlation, just a topic switch.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..engine.tracker import serialise
from ..realtime import TOPIC_SESSION, TOPIC_SIMULATIONS
from ..state import AppState

log = structlog.get_logger(__name__)

router = APIRouter(tags=["realtime"])

#: Nudge the socket periodically so a dead connection is noticed rather than lingering.
HEARTBEAT_SECONDS = 25.0


@router.websocket("/ws")
async def telemetry(websocket: WebSocket) -> None:
    # WebSocket routes get no Request, so reach the composition root off the app.
    state: AppState = websocket.app.state.harness
    await state.hub.connect(websocket)

    try:
        # Send current state immediately so a newly opened tab is not blank until
        # something happens to change.
        await websocket.send_json({"topic": TOPIC_SESSION, "payload": state.auth.session.to_dict()})
        await websocket.send_json(
            {
                "topic": TOPIC_SIMULATIONS,
                "payload": [serialise(r) for r in await state.tracker.active()],
            }
        )

        while True:
            try:
                # Any inbound frame is treated as a keepalive; there are no commands.
                await asyncio.wait_for(websocket.receive_text(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                await websocket.send_json({"topic": "ping", "payload": None})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("ws.closed_unexpectedly", exc_info=True)
    finally:
        await state.hub.disconnect(websocket)
        with contextlib.suppress(Exception):
            await websocket.close()
