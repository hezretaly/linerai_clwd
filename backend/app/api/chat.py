"""Buyer-facing chat. Public -- no login.

The assistant reply streams over SSE. Rails send their message_text as an
ordinary buyer message through this same endpoint: one code path, guards still
apply, and the transcript reads identically whether the buyer typed or tapped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent import tools
from app.agent.runner import (
    rails_for,
    record_assistant_message,
    record_buyer_message,
    run_agent_turn,
)
from app.agent.tools import when_label
from app.api.settings import live_settings
from app.config import settings
from app.db import SessionLocal, get_db
from app.brand import brand
from app.events import emit
from app.integrations.base import NotConfigured
from app.models import Appointment, Conversation, Dealership, Rail
from app.schemas.serialize import (
    appointment_out,
    booking_card,
    conversation_out,
    message_out,
    rail_out,
)

log = logging.getLogger("liner.chat")

router = APIRouter(prefix="/chat", tags=["chat"])

# Tokens are released in small groups so the buyer sees the reply build rather
# than appear. 400-900 ms of typing indicator happens on the client.
STREAM_CHUNK_WORDS = 3
STREAM_DELAY_S = 0.045

# A knowledge answer comes back in milliseconds because it is a table lookup.
# Arriving instantly reads as canned -- the buyer sees the reply before they
# have finished reading their own message. This holds the typing indicator for
# a beat first. It is a floor, not a sleep: a turn that took longer than this
# (a live model call, a tool round) waits no extra time at all.
MIN_THINKING_S = 0.7


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
        "dealership": {
            "name": dealership.name if dealership else "",
            # Their colours, so the buyer's screen looks like the site they
            # came from rather than like our demo. Served rather than stored:
            # see app/brand.py.
            "brand": brand(),
        },
        "rails": [rail_out(r) for r in rails_for(db, convo)],
    }


@router.get("/sessions/{conversation_id}")
def rehydrate(conversation_id: str, db: Session = Depends(get_db)) -> dict:
    """The whole thread back, including the cards.

    A refresh used to lose the conversation entirely. The messages were always
    here; what was missing was everything the buyer had been *shown* -- the
    search results and the booking card live in the reply's tool calls, so the
    client can rebuild them from ``messages[].tool_calls`` rather than from
    memory it no longer has.

    Availability is the exception and is looked up again rather than replayed.
    A slot list from ten minutes ago is a list of times that may be gone, and
    re-offering one is how a buyer picks a time that no longer exists.
    """
    convo = _conversation(db, conversation_id)
    out = conversation_out(convo, db, detail=True)
    out["rails"] = [rail_out(r) for r in rails_for(db, convo)]
    # The opening line is client-side only -- it is never a message row -- so a
    # rehydrated thread would start abruptly at the buyer's first question.
    out["greeting"] = live_settings(db).greeting

    out["booking"] = None
    offered = any(
        call.get("name") == "check_availability"
        for message in out["messages"]
        for call in message["tool_calls"]
    )
    if offered and convo.stage != "booked":
        fresh = tools.check_availability(db, convo, {})
        if fresh["slots"]:
            out["booking"] = booking_card(fresh["slots"], fresh["slot_minutes"])
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
        started = time.monotonic()
        session = SessionLocal()
        try:
            convo_local = session.query(Conversation).filter_by(id=convo_id).one()
            try:
                message = await asyncio.to_thread(run_agent_turn, session, convo_local, text)
            except NotConfigured as exc:
                # LLM_MODE=live with no key, or a key the vendor rejected. The
                # response has already started, so an exception here cannot
                # become a 503 -- FastAPI raises "response already started" and
                # the buyer watches a typing indicator that never resolves.
                # Say it on the stream instead, and name the setting.
                log.error("live agent unavailable: %s", exc.as_dict())
                yield _sse("error", {
                    "message": "The assistant is not available right now. "
                               "Someone from the team will pick this up.",
                    **exc.as_dict(),
                })
                yield _sse("done", {"stage": convo_local.stage})
                return
            except Exception as exc:  # a vendor outage, a rate limit, a timeout
                log.exception("agent turn failed on conversation %s", convo_id)
                yield _sse("error", {
                    "message": "Something went wrong answering that. "
                               "Someone from the team will pick this up.",
                    "detail": str(exc)[:200],
                })
                yield _sse("done", {"stage": convo_local.stage})
                return
            if message is None:
                yield _sse("held", {"message": "Liner is holding this conversation."})
                yield _sse("done", {"stage": convo_local.stage})
                return

            payload = message_out(message)
            elapsed = time.monotonic() - started
            if elapsed < MIN_THINKING_S:
                await asyncio.sleep(MIN_THINKING_S - elapsed)

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

            # A booking card, built from what check_availability actually
            # returned. Two flat chips used to go out here and the buyer had to
            # type the rest, which is where bookings were lost: the model then
            # had to read a name, an email and a time back out of prose.
            avail = next(
                (
                    call.get("result", {})
                    for call in payload["tool_calls"]
                    if call.get("name") == "check_availability"
                ),
                None,
            )
            if avail and avail.get("slots"):
                yield _sse(
                    "booking",
                    booking_card(avail["slots"], avail.get("slot_minutes") or 30),
                )

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


class BookingForm(BaseModel):
    starts_at: str
    name: str
    email: str
    phone: str = ""
    vin: str | None = None


@router.post("/sessions/{conversation_id}/book")
def book_from_card(
    conversation_id: str, body: BookingForm, db: Session = Depends(get_db)
) -> dict:
    """The booking card's submit. Books through the same executor the model uses.

    Not a shortcut around the agent: ``book_appointment`` is where the hours
    check, the slot-clash check, the email format rule and the lead matching
    live, so a form that wrote its own Appointment row would be a second set of
    rules to keep in step. The form's advantage is only that a tapped time and
    a typed email arrive as fields instead of as prose the model has to parse
    back out -- which is where bookings were being dropped.
    """
    convo = _conversation(db, conversation_id)

    # Idempotent per (conversation, slot): a double-tapped submit or a retried
    # request returns the appointment already made rather than a second one.
    call_id = f"form-{convo.id}-{body.starts_at}"
    try:
        result = tools.execute(
            db,
            convo,
            "book_appointment",
            {
                "starts_at": body.starts_at,
                "name": body.name,
                "email": body.email,
                "phone": body.phone,
                **({"vin": body.vin} if body.vin else {}),
            },
            call_id,
        )
    except tools.ToolError as exc:
        # 409, not 500: the slot went, or the hours do not allow it. The card
        # shows this text and asks again -- these are all things the buyer can
        # act on, and none of them are a bug.
        raise HTTPException(409, str(exc)) from None

    starts_at = datetime.fromisoformat(result["starts_at"])
    contact = " -- ".join(p for p in (body.name.strip(), body.email.strip(), body.phone.strip()) if p)
    # Written as the buyer's own words on purpose. It keeps the transcript
    # readable for the rep, gives the model the contact details on later turns,
    # and it is what save_captured_fields checks against before it will accept
    # a value as 'typed' rather than downgrading it to a guess.
    buyer_message = record_buyer_message(
        db, convo, f"{when_label(starts_at)} works. {contact}"
    )

    # What actually happens next, and nothing beyond it. The appointment is
    # created unassigned (book_appointment sets no rep) and confirming is a
    # person pressing Confirm on the dashboard, so "a rep will pick this up and
    # confirm" is the truth. The sentence about email is conditional on the
    # sender that actually delivers: with EMAIL_SENDER=outbox nothing leaves
    # the building, and promising a confirmation that never arrives is the one
    # way a booked buyer stops trusting the whole thread.
    reply = (
        f"You're booked in for {when_label(starts_at)}. It's on the calendar "
        f"now -- one of our team picks it up from here and confirms with you "
        f"before then."
    )
    if settings.email_sender == "gmail":
        reply += f" The confirmation goes to {body.email.strip()}."
    assistant_message = record_assistant_message(
        db, convo, reply, [{"name": "book_appointment", "input": {}, "result": result}]
    )

    appointment = db.query(Appointment).filter_by(id=result["appointment_id"]).one()
    return {
        "appointment": appointment_out(appointment, db),
        "buyer_message": message_out(buyer_message),
        "assistant_message": message_out(assistant_message),
        "rails": [rail_out(r) for r in rails_for(db, convo)],
        "stage": convo.stage,
    }


def _vehicles_from(result: dict) -> list[dict]:
    if "vehicles" in result:
        return result["vehicles"]
    if "vin" in result:
        return [result]
    return []


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
