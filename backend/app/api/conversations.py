from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from datetime import datetime

from app import timeline
from app.recap import conversation_recap
from app.agent import tools
from app.agent.tools import when_label
from app.api.deps import current_user
from app.db import get_db, utcnow
from app.events import emit
from app.models import Conversation, Escalation, Lead, Message, User, Vehicle
from app.schemas.serialize import (
    booking_card, conversation_out, message_out, stamp, vehicle_out,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _get(db: Session, conversation_id: str) -> Conversation:
    convo = db.query(Conversation).filter_by(id=conversation_id).one_or_none()
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    return convo


@router.get("")
def list_conversations(
    status: str | None = Query(None),
    channel: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    # A session where the buyer never said anything is not a conversation --
    # opening the chat widget and closing it should not reach the dealer.
    started = (
        db.query(Message.conversation_id)
        .filter(Message.role == "buyer")
        .distinct()
        .subquery()
    )
    query = db.query(Conversation).filter(Conversation.id.in_(select(started)))
    if status:
        query = query.filter(Conversation.status == status)
    if channel:
        query = query.filter(Conversation.channel == channel)
    rows = query.all()

    # One grouped query, not one per row. Sorting on started_at put a thread
    # that opened this morning and has been silent since above one that a buyer
    # is typing in right now, which is the wrong way round for a list whose
    # whole job is "what is happening".
    last_activity = dict(
        db.query(Message.conversation_id, func.max(Message.created_at))
        .filter(Message.conversation_id.in_([c.id for c in rows] or [""]))
        .group_by(Message.conversation_id)
        .all()
    )

    def activity_of(convo: Conversation) -> datetime:
        return last_activity.get(convo.id) or convo.started_at

    # Same trick for the car each thread settled on: the list shows it in a
    # column, and looking it up per row would be a query each on a page that
    # already makes several.
    focus_ids = {c.focus_vehicle_id for c in rows if c.focus_vehicle_id}
    focus = {
        v.id: vehicle_out(v)
        for v in (
            db.query(Vehicle).filter(Vehicle.id.in_(focus_ids)).all() if focus_ids else []
        )
    }

    rows.sort(key=activity_of, reverse=True)
    return {
        "conversations": [
            {
                **conversation_out(c, db),
                "last_activity_at": stamp(activity_of(c)),
                "focus_vehicle": focus.get(c.focus_vehicle_id or ""),
            }
            for c in rows
        ]
    }


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    return conversation_out(_get(db, conversation_id), db, detail=True)


@router.get("/{conversation_id}/timeline")
def get_timeline(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """One thread, in the same shape the lead page renders.

    For a conversation with no lead yet. An anonymous chat is still something a
    rep has to read and answer, and there is no buyer to hang a timeline on
    until someone books -- so it gets its own rather than being invisible.
    """
    convo = _get(db, conversation_id)
    entries = timeline.conversation_timeline(db, convo)
    return {
        "lead": None,
        "entries": entries,
        "channels": timeline.channel_counts(entries),
        "conversations": [conversation_out(convo, db)],
        "recap": conversation_recap(db, convo),
        "reply_to": None if convo.status == "closed" else convo.id,
    }


@router.post("/{conversation_id}/takeover")
def takeover(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """A rep enters the thread. agent_paused is what actually stops Liner --
    it is deliberately separate from status (§18.2)."""
    convo = _get(db, conversation_id)
    convo.agent_paused = True
    convo.status = "handoff"

    escalation = (
        db.query(Escalation)
        .filter_by(conversation_id=convo.id, claimed_at=None)
        .order_by(Escalation.created_at.desc())
        .first()
    )
    if escalation is not None:
        escalation.claimed_by_user_id = user.id
        escalation.claimed_at = utcnow()

    # Taking a thread over takes the buyer with it, so they stop showing as
    # unclaimed on the overview while somebody is visibly typing to them. Only
    # when nobody owns them yet: silently moving a buyer off the rep they were
    # assigned to would be a different act, and one nobody asked for.
    lead = db.query(Lead).filter_by(id=convo.lead_id).one_or_none() if convo.lead_id else None
    if lead is not None and not lead.assigned_user_id:
        lead.assigned_user_id = user.id

    db.commit()
    emit(db, "handoff.triggered", {
        "conversation_id": convo.id, "claimed_by": user.id, "action": "takeover",
    })
    return conversation_out(convo, db, detail=True)


@router.post("/{conversation_id}/handback")
def handback(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    convo = _get(db, conversation_id)
    convo.agent_paused = False
    convo.status = "active"
    db.commit()
    return conversation_out(convo, db, detail=True)


@router.post("/{conversation_id}/decline")
def decline(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """The buyer said no. Closes the thread and records *why* it closed.

    Separate from the generic close because "closed" already covers a buyer
    who booked, a buyer who wandered off and a buyer who declined, and a queue
    that cannot tell them apart cannot be filtered on any of them.
    """
    convo = _get(db, conversation_id)
    convo.outcome = "declined"
    convo.status = "closed"
    convo.agent_paused = True
    convo.ended_at = convo.ended_at or utcnow()

    # A declined thread is not still waiting for a person.
    for escalation in (
        db.query(Escalation)
        .filter(Escalation.conversation_id == convo.id, Escalation.claimed_at.is_(None))
        .all()
    ):
        escalation.claimed_by_user_id = user.id
        escalation.claimed_at = utcnow()

    db.commit()
    emit(db, "conversation.declined", {"conversation_id": convo.id, "by": user.id})
    return conversation_out(convo, db, detail=True)


@router.get("/{conversation_id}/availability")
def availability(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """The same day/time card the buyer is offered, for a rep booking on their
    behalf. Looked up now, never replayed -- see api/chat.py."""
    convo = _get(db, conversation_id)
    fresh = tools.check_availability(db, convo, {})
    return booking_card(fresh["slots"], fresh["slot_minutes"])


class RepBooking(BaseModel):
    starts_at: str
    name: str
    email: str
    phone: str = ""


@router.post("/{conversation_id}/book")
def book_for_buyer(
    conversation_id: str,
    body: RepBooking,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """A rep books the appointment themselves, through the buyer's executor.

    Not a second booking path: `book_appointment` owns the hours rule, the
    clash check and the lead matching, so a rep who books here is held to
    exactly what Liner is held to. Only `booked_by` differs, because who made
    the appointment is a fact worth keeping.
    """
    convo = _get(db, conversation_id)
    try:
        # The executor directly, not tools.execute: that path validates argument
        # names against the schema the *model* is given, and `booked_by` is
        # deliberately not in it -- Liner must not be able to claim a rep made
        # the booking. Same function, same rules, one argument the model cannot
        # reach.
        result = tools.book_appointment(
            db,
            convo,
            {
                "starts_at": body.starts_at, "name": body.name,
                "email": body.email, "phone": body.phone, "booked_by": "rep",
            },
            # Keyed on the conversation, not the rep: two reps booking the
            # same slot for two different buyers must clash, and a repeat
            # submit for this buyer must return what was already made.
            f"rep-{convo.id}-{body.starts_at}",
        )
    except tools.ToolError as exc:
        raise HTTPException(409, str(exc)) from None

    message = Message(
        conversation_id=convo.id, role="rep",
        content=f"Booked {when_label(datetime.fromisoformat(result['starts_at']))} "
                f"for {body.name}.",
    )
    db.add(message)
    db.commit()
    return conversation_out(convo, db, detail=True)


class RepMessage(BaseModel):
    content: str


@router.post("/{conversation_id}/messages")
def rep_reply(
    conversation_id: str,
    body: RepMessage,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """A rep replies into the buyer's thread. The buyer sees it as coming from
    the dealership, which is why the composer shows the "You are replying as
    Riverside Auto" bar."""
    convo = _get(db, conversation_id)
    if not convo.agent_paused:
        raise HTTPException(
            409,
            "Liner still owns this conversation. Take it over before replying, so you and "
            "the assistant do not answer the buyer at the same time.",
        )
    message = Message(conversation_id=convo.id, role="rep", content=body.content.strip())
    db.add(message)
    db.commit()
    db.refresh(message)
    emit(db, "conversation.message", {
        "conversation_id": convo.id, "message_id": message.id, "role": "rep",
    })
    return message_out(message)
