"""Buyer-facing chat. Public -- no login.

The assistant reply streams over SSE. Rails send their message_text as an
ordinary buyer message through this same endpoint: one code path, guards still
apply, and the transcript reads identically whether the buyer typed or tapped.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.runner import rails_for, record_buyer_message, run_agent_turn
from app.api.settings import live_settings
from app.db import SessionLocal, get_db
from app.events import emit
from app.models import Conversation, Dealership, Rail
from app.schemas.serialize import conversation_out, message_out, rail_out

router = APIRouter(prefix="/chat", tags=["chat"])

# Tokens are released in small groups so the buyer sees the reply build rather
# than appear. 400-900 ms of typing indicator happens on the client.
STREAM_CHUNK_WORDS = 3
STREAM_DELAY_S = 0.045


def _conversation(db: Session, conversation_id: str) -> Conversation:
    convo = db.query(Conversation).filter_by(id=conversation_id).one_or_none()
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    return convo


@router.post("/sessions")
def start_session(channel: str = "chat", db: Session = Depends(get_db)) -> dict:
    convo = Conversation(channel=channel, status="active", stage="opening")
    db.add(convo)
    db.commit()
    db.refresh(convo)

    dealership = db.query(Dealership).first()
    settings_row = live_settings(db)
    emit(db, "conversation.started", {"conversation_id": convo.id, "channel": channel})

    return {
        "conversation_id": convo.id,
        "greeting": settings_row.greeting,
        "dealership": {"name": dealership.name if dealership else "", },
        "rails": [rail_out(r) for r in rails_for(db, convo)],
    }


@router.get("/sessions/{conversation_id}")
def rehydrate(conversation_id: str, db: Session = Depends(get_db)) -> dict:
    convo = _conversation(db, conversation_id)
    out = conversation_out(convo, db, detail=True)
    out["rails"] = [rail_out(r) for r in rails_for(db, convo)]
    return out


@router.get("/sessions/{conversation_id}/rails")
def get_rails(conversation_id: str, db: Session = Depends(get_db)) -> dict:
    convo = _conversation(db, conversation_id)
    return {"stage": convo.stage, "rails": [rail_out(r) for r in rails_for(db, convo)]}


class BuyerMessage(BaseModel):
    content: str | None = None
    rail_id: str | None = None


@router.post("/sessions/{conversation_id}/messages")
async def send_message(
    conversation_id: str, body: BuyerMessage, db: Session = Depends(get_db)
) -> StreamingResponse:
    convo = _conversation(db, conversation_id)

    text = (body.content or "").strip()
    rail_id = body.rail_id
    if rail_id:
        rail = db.query(Rail).filter_by(id=rail_id, enabled=True).one_or_none()
        if rail is None:
            raise HTTPException(404, "Rail not found")
        # Tapping a chip is exactly the same as typing its text.
        text = rail.message_text
    if not text:
        raise HTTPException(400, "Empty message")

    buyer_message = record_buyer_message(db, convo, text, rail_id)
    paused = convo.agent_paused
    convo_id = convo.id

    async def stream():
        yield _sse("buyer_message", message_out(buyer_message))

        if paused:
            # A rep owns this thread. Say so rather than letting the buyer sit
            # in front of a typing indicator that never resolves.
            yield _sse("held", {
                "message": "Someone from the team is picking this up personally.",
            })
            yield _sse("done", {"stage": "escalated"})
            return

        # The agent turn is synchronous DB work; keep the event loop free.
        session = SessionLocal()
        try:
            convo_local = session.query(Conversation).filter_by(id=convo_id).one()
            message = await asyncio.to_thread(run_agent_turn, session, convo_local, text)
            if message is None:
                yield _sse("held", {"message": "Liner is holding this conversation."})
                yield _sse("done", {"stage": convo_local.stage})
                return

            payload = message_out(message)
            words = message.content.split(" ")
            for i in range(0, len(words), STREAM_CHUNK_WORDS):
                yield _sse("token", {"text": " ".join(words[i:i + STREAM_CHUNK_WORDS]) + " "})
                await asyncio.sleep(STREAM_DELAY_S)

            yield _sse("assistant_message", payload)

            vehicles = [
                v
                for call in payload["tool_calls"]
                if call.get("name") in {"search_inventory", "get_vehicle"}
                for v in _vehicles_from(call.get("result", {}))
            ]
            if vehicles:
                yield _sse("vehicles", {"vehicles": vehicles[:3]})

            slots = [
                s
                for call in payload["tool_calls"]
                if call.get("name") == "check_availability"
                for s in (call.get("result", {}).get("slots") or [])[:2]
            ]
            if slots:
                yield _sse("slots", {"slots": slots})

            session.refresh(convo_local)
            yield _sse("rails", {
                "stage": convo_local.stage,
                "rails": [rail_out(r) for r in rails_for(session, convo_local)],
            })
            yield _sse("done", {"stage": convo_local.stage})
        finally:
            session.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _vehicles_from(result: dict) -> list[dict]:
    if "vehicles" in result:
        return result["vehicles"]
    if "vin" in result:
        return [result]
    return []


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
