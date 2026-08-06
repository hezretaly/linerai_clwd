from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db import get_db, utcnow
from app.events import emit
from app.models import Conversation, Escalation, Message, User
from app.schemas.serialize import conversation_out, message_out

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
    query = db.query(Conversation)
    if status:
        query = query.filter(Conversation.status == status)
    if channel:
        query = query.filter(Conversation.channel == channel)
    rows = query.order_by(Conversation.started_at.desc()).all()
    return {"conversations": [conversation_out(c, db) for c in rows]}


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    return conversation_out(_get(db, conversation_id), db, detail=True)


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
