"""Dealer event socket.

Connect with ``?since={event_id}`` and the backlog replays from the events
table before live events start -- a dashboard that was closed during a booking
catches up instead of refetching everything.

On the client each event invalidates the relevant query keys rather than
patching cache by hand. Simpler, and the refetch is cheap against SQLite.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from itsdangerous import BadSignature

from app.api.deps import resolve_account, serializer
from app.config import settings
from app.db import SessionLocal
from app.events import manager, replay

log = logging.getLogger("liner.ws")

router = APIRouter()


@router.websocket("/ws/dealer")
async def dealer_socket(websocket: WebSocket, since: int = Query(0)) -> None:
    raw = websocket.cookies.get(settings.session_cookie)
    data: dict = {}
    if raw:
        try:
            data = serializer.loads(raw)
        except BadSignature:
            data = {}

    db = SessionLocal()
    try:
        # Either realm. The socket carries dealership events *and* ours, and
        # `/ops` is the one dashboard with something on it nobody clicked for
        # -- resolving only `users` closed the connection for an ops session
        # and took the demo notification with it.
        user = resolve_account(db, data) if data else None
        if user is None:
            await websocket.close(code=4401, reason="Not signed in")
            return

        await manager.connect(websocket)
        for event in replay(db, since):
            await websocket.send_json(event)
        await websocket.send_json({"type": "ready", "id": since, "payload": {"user": user.name}})
    finally:
        db.close()

    try:
        while True:
            # The dealer socket is push-only; this keeps the connection open
            # and surfaces client disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
