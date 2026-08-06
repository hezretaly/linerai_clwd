"""Event emission: one DB row plus a broadcast to every connected dealer socket.

The connection manager is in-process, so the backend must run as a single
uvicorn worker (§5.2). That is asserted at startup. When more than one worker is
needed this file becomes Redis pub/sub and nothing else changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Event

log = logging.getLogger("liner.events")

EVENT_TYPES = {
    "conversation.started",
    "conversation.message",
    "lead.qualified",
    "appointment.booked",
    "appointment.confirmed",
    "appointment.assigned",
    "handoff.triggered",
    "outreach.sent",
    "call.started",
    "call.ended",
}


class ConnectionManager:
    def __init__(self) -> None:
        self._sockets: set[Any] = set()

    async def connect(self, websocket: Any) -> None:
        await websocket.accept()
        self._sockets.add(websocket)

    def disconnect(self, websocket: Any) -> None:
        self._sockets.discard(websocket)

    @property
    def count(self) -> int:
        return len(self._sockets)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for socket in list(self._sockets):
            try:
                await socket.send_json(message)
            except Exception:
                dead.append(socket)
        for socket in dead:
            self.disconnect(socket)


manager = ConnectionManager()


def emit(db: Session, type_: str, payload: dict | None = None) -> Event:
    """Write the event row and push it to connected dashboards.

    Safe to call from sync request handlers: if an event loop is running the
    broadcast is scheduled on it, otherwise the row is still persisted and any
    dashboard picks it up on its next ``?since=`` replay.
    """
    if type_ not in EVENT_TYPES:
        log.warning("emitting unregistered event type %r", type_)

    event = Event(type=type_, payload_json=json.dumps(payload or {}, default=str))
    db.add(event)
    db.commit()
    db.refresh(event)

    message = {
        "id": event.id,
        "type": event.type,
        "payload": payload or {},
        "created_at": event.created_at.isoformat(),
    }
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return event
    loop.create_task(manager.broadcast(message))
    return event


def replay(db: Session, since: int = 0, limit: int = 200) -> list[dict]:
    rows = (
        db.query(Event)
        .filter(Event.id > since)
        .order_by(Event.id.asc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "type": r.type,
            "payload": json.loads(r.payload_json or "{}"),
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
