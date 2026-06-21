"""WebSocket endpoint + connection manager.

Broadcasts on each control-loop tick and after any change. Per the agreed
"superset" contract, every update is emitted in BOTH message-type styles:

  * written spec:  plc:reading / controller:state / miners:update
  * live frontend: plc_reading_update / controller_state_update / miner_status_update

so clients built to either contract work unmodified.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.schemas import ControllerStateOut
from app.services.controller import controller

router = APIRouter(tags=["ws"])


class ConnectionManager:
    """Tracks connected clients and fans out messages, dropping dead sockets."""

    def __init__(self) -> None:
        """Initialise with an empty set of active connections."""
        self.active: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket and register it for broadcasts."""
        await websocket.accept()
        self.active.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from the active set (no-op if absent)."""
        self.active.discard(websocket)

    async def broadcast(self, message: dict) -> None:
        """Send a message to every client, dropping any that error out."""
        dead: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.discard(ws)


manager = ConnectionManager()


def _snapshot() -> tuple[dict | None, dict, list[dict]]:
    """Serialize the latest reading, controller state and miners to camelCase."""
    readings = controller.recent_readings()
    reading = readings[-1] if readings else None
    reading_data = (
        reading.model_dump(by_alias=True, mode="json") if reading is not None else None
    )
    state_data = ControllerStateOut.from_state(controller.state).model_dump(mode="json")
    miners_data = [
        m.model_dump(by_alias=True, mode="json") for m in controller.miners.list_miners()
    ]
    return reading_data, state_data, miners_data


def _messages() -> list[dict]:
    """Build the full set of broadcast messages in both type styles."""
    reading_data, state_data, miners_data = _snapshot()
    ts = datetime.now(timezone.utc).isoformat()
    messages: list[dict] = []
    if reading_data is not None:
        messages.append({"type": "plc:reading", "data": reading_data, "timestamp": ts})
        messages.append({"type": "plc_reading_update", "data": reading_data, "timestamp": ts})
    messages.append({"type": "controller:state", "data": state_data, "timestamp": ts})
    messages.append({"type": "controller_state_update", "data": state_data, "timestamp": ts})
    messages.append({"type": "miners:update", "data": miners_data, "timestamp": ts})
    messages.append({"type": "miner_status_update", "data": miners_data, "timestamp": ts})
    return messages


async def broadcast_snapshot() -> None:
    """Push the current PLC reading, controller state and miners to all clients."""
    for message in _messages():
        await manager.broadcast(message)


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """Accept a client, send an initial snapshot, and keep it subscribed."""
    await manager.connect(websocket)
    try:
        # Send an immediate snapshot so a freshly-connected client is in sync.
        for message in _messages():
            await websocket.send_json(message)
        # Keep the connection open; we don't expect inbound messages.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
