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
from app.schemas.serialize import stamp

log = logging.getLogger("liner.events")

EVENT_TYPES = {
    "conversation.started",
    "conversation.message",
    "lead.qualified",
    "appointment.booked",
    "appointment.confirmed",
    "appointment.cancelled",
    # Moved, not cancelled-and-rebooked. The distinction is the whole point:
    # one is a buyer changing their day, the other looks like losing them.
    "appointment.rescheduled",
    "appointment.assigned",
    "handoff.triggered",
    "conversation.declined",
    "outreach.sent",
    "outreach.opened",
    # Mail that arrived and got filed -- accepted or unresolved, both after the
    # HMAC passed. Refusals are deliberately silent: the receipt is written
    # before authentication, and emitting there would let anyone who found the
    # URL grow the events table.
    "email.received",
    "lead.imported",
    # **These three predate the SMS work and were never registered**, so every
    # one of them logged "emitting unregistered event type" and went out
    # anyway. Found by the gate check that now reads this set against every
    # `emit()` call site -- the same trick `SPA_PREFIXES` needed, because a
    # hand-written list is exactly what development cannot check.
    #
    # A buyer's row changed under a rep who may be looking at it: a name
    # learned from an email signature, an address a rep linked.
    "lead.updated",
    # An email from somebody nobody had on file minted a buyer.
    "lead.created",
    # Whether Liner answers email was switched, which every open dashboard
    # needs to agree about immediately.
    "email.agent",
    # Somebody asked us for a demo. Ours, not a dealership's -- it is the one
    # event on this system that nobody clicked for and that we have to act on.
    "demo.requested",
    "demo.updated",
    # A buyer got an owner, which also settles anything of theirs that was
    # sitting in the unowned queue -- so three panels move on one event.
    "lead.assigned",
    # Somebody left, and their buyers went back to the queue rather than with
    # them. Three panels change, so it is worth a socket event.
    "team.deactivated",
    "vehicle.status_changed",
    "call.started",
    "call.ended",
    # A call's transcript has been rewritten from its own recording, which
    # happens minutes after the call ended and while a rep may be reading it.
    "call.transcribed",
    # Liner's own Twilio number. `phone.*` is a real telephone call in or out;
    # `call.*` above is the browser's WebRTC call on /call, and they are
    # deliberately not the same name.
    "phone.started",
    "phone.ended",
    "phone.persona",
    # Texts. `sms.received` fires for one that resolved to nobody as well as
    # one that landed on a buyer -- a stranger's text has no buyer page to
    # appear on instead, the same reason `email.received` does it.
    "sms.sent",
    "sms.received",
    "sms.status",
    # Somebody asked to stop being texted, or to start again. Worth an event
    # because it changes what every composer on the dashboard may do next.
    "sms.opt_out",
    "sms.resumed",
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

# The main event loop, captured at startup. Most endpoints here are sync `def`,
# which FastAPI runs in a threadpool -- there is no running loop in that thread,
# so the broadcast has to be handed back to the main one explicitly. Without
# this, events reach the database and never reach a dashboard.
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def _schedule(message: dict) -> None:
    loop = _loop
    if loop is None or loop.is_closed():
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is loop:
        loop.create_task(manager.broadcast(message))
    else:
        asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)


def emit_ops(type_: str, payload: dict | None = None) -> Event:
    """Announce something that happened on Liner's own side.

    **The event stream is deliberately not split, even though the rows are.**
    `ops_*` tables moved to their own database; `events` did not, and must not:
    `events.id` is the one autoincrement integer in this schema because the
    socket replays with `?since=<id>`, and that cursor only means anything
    against a single monotonic sequence. Two event tables would be two
    sequences whose ids collide and interleave, and a dashboard reconnecting
    with `?since=41` could not say which 41 it meant -- so it would either
    replay events it has seen or skip ones it has not.

    So an ops row is written to `ops.db` and the *announcement* of it goes
    where every other announcement goes: the default store's `events`, which
    is the table `/ws/dealer` already replays from for both realms.
    """
    from app.db import SessionLocal

    with SessionLocal("") as db:
        return emit(db, type_, payload)


def emit(db: Session, type_: str, payload: dict | None = None) -> Event:
    """Write the event row and push it to connected dashboards.

    Safe to call from sync handlers and background threads. If the broadcast
    cannot be scheduled the row is still persisted, and any dashboard catches
    up on its next ``?since=`` replay.
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
        "created_at": stamp(event.created_at),
    }
    _schedule(message)
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
            "created_at": stamp(r.created_at),
        }
        for r in rows
    ]
