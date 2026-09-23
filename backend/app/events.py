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
    """Every open dashboard socket, tagged with the store it was opened for.

    **A live event only reaches the dashboards of the store it happened
    in.** `replay` was always store-scoped -- it reads that store's `events`
    table -- but the live push went to every socket in the process, so once
    one intake URL could file a delivery into Alsbou's store, Craig's
    dashboard received `email.received` naming a lead id that exists only in
    Alsbou's file, refetched it, and 404ed. The tag is the store the socket
    connected under (`/alsbou/ws/dealer` is Alsbou's; an unprefixed socket is
    the default store's, which is also where `/ops` listens and where
    `emit_ops` announces).
    """

    def __init__(self) -> None:
        self._sockets: dict[Any, str] = {}

    @staticmethod
    def key(store: str = "") -> str:
        """What tells two stores apart: the database they open, not the slug.

        With `DEALERSHIP=alsbou` the unprefixed dashboard and `/alsbou/app`
        read one file, and an event raised through either has to reach both.
        Keyed on the slug they would be two audiences for one store.
        """
        from app.db import engine_for

        return str(engine_for((store or "").strip()).url)

    async def connect(self, websocket: Any, store: str = "") -> None:
        await websocket.accept()
        self._sockets[websocket] = self.key(store)

    def disconnect(self, websocket: Any) -> None:
        self._sockets.pop(websocket, None)

    @property
    def count(self) -> int:
        return len(self._sockets)

    def listening(self, store: str = "") -> int:
        """How many sockets an event in this store would reach."""
        wanted = self.key(store)
        return sum(1 for s in self._sockets.values() if s == wanted)

    async def broadcast(self, message: dict, store: str = "") -> None:
        wanted = self.key(store)
        dead = []
        for socket, tag in list(self._sockets.items()):
            if tag != wanted:
                continue
            try:
                await socket.send_json(message)
            except Exception:
                dead.append(socket)
        for socket in dead:
            self.disconnect(socket)


manager = ConnectionManager()


class ThreadWatchers:
    """Buyer pages holding a live stream open on their own conversation.

    **The buyer's `/chat` had no way to hear anything it had not asked for.**
    Its only stream was the reply to its own POST, so a rep who took a thread
    over and answered wrote a row the buyer never saw -- until they refreshed,
    which nobody waiting on an answer thinks to do. They read the silence as
    being ignored, and the rep reads the silence back as a buyer who left.

    Kept apart from `ConnectionManager` on purpose. That one pushes the whole
    event stream to signed-in staff; this one pushes nothing at all. A waiter
    is an `asyncio.Event` keyed on a conversation id, and setting it only
    tells that page's stream to go and *look* -- what the buyer is then sent
    is read from the rows and cut to their shape in `api/chat.py`, so no
    event payload (an escalation's reason, a rep's id) can reach a stranger's
    browser through here.

    Keyed on the id alone, not the store: ids are UUIDs, and the worst a
    collision could do is make a page look at its own thread for nothing.
    """

    #: Per conversation. A buyer has one page open, perhaps two; more than
    #: this is a script holding connections, and each one is a socket.
    PER_THREAD = 4
    #: Across the process. Well above any real afternoon, and below the point
    #: where held connections would starve the ones doing work.
    TOTAL = 2000

    def __init__(self) -> None:
        self._waiting: dict[str, set[asyncio.Event]] = {}

    @property
    def count(self) -> int:
        return sum(len(v) for v in self._waiting.values())

    def full(self, conversation_id: str) -> bool:
        """Whether either ceiling is reached for this thread."""
        return (
            len(self._waiting.get(conversation_id, ())) >= self.PER_THREAD
            or self.count >= self.TOTAL
        )

    def watch(self, conversation_id: str) -> asyncio.Event | None:
        """A waiter for this thread, or None when either ceiling is reached."""
        if self.full(conversation_id):
            return None
        waiter = asyncio.Event()
        self._waiting.setdefault(conversation_id, set()).add(waiter)
        return waiter

    def unwatch(self, conversation_id: str, waiter: asyncio.Event) -> None:
        current = self._waiting.get(conversation_id)
        if current is None:
            return
        current.discard(waiter)
        if not current:
            self._waiting.pop(conversation_id, None)

    def wake(self, conversation_id: str) -> None:
        for waiter in list(self._waiting.get(conversation_id, ())):
            waiter.set()


watchers = ThreadWatchers()

# The main event loop, captured at startup. Most endpoints here are sync `def`,
# which FastAPI runs in a threadpool -- there is no running loop in that thread,
# so the broadcast has to be handed back to the main one explicitly. Without
# this, events reach the database and never reach a dashboard.
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def _schedule(message: dict, store: str = "") -> None:
    loop = _loop
    if loop is None or loop.is_closed():
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is loop:
        loop.create_task(manager.broadcast(message, store))
    else:
        asyncio.run_coroutine_threadsafe(manager.broadcast(message, store), loop)


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
    # Pushed to the dashboards of the store the row was written in. The
    # session says which -- its engine is one store's file -- so an event
    # written through `emit_ops`' explicit default-store session, or through
    # a routed intake session, goes to that store's audience and not to
    # whichever store the request happened to arrive for.
    _schedule(message, _store_of(db))
    # Anything about a conversation sends that buyer's open page to look at
    # its own thread. Whatever it finds is read from rows, never from this
    # payload (`ThreadWatchers`).
    conversation_id = (payload or {}).get("conversation_id")
    if conversation_id:
        _wake(str(conversation_id))
    return event


def _wake(conversation_id: str) -> None:
    loop = _loop
    if loop is None or loop.is_closed():
        return
    loop.call_soon_threadsafe(watchers.wake, conversation_id)


def _store_of(db: Session) -> str:
    """The slug whose file this session is on, as `ConnectionManager.key`
    understands it -- "" for the default store."""
    from app.db import active_store, engine_for

    bind = db.get_bind()
    if bind is engine_for(""):
        return ""
    return active_store()


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
