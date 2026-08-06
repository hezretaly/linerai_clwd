"""Turn orchestration -- the loop described in §5.3.

1. Persist the buyer message (with via_rail_id when it came from a chip).
2. If agent_paused, stop. The rep owns the thread.
3. Run the agent (stub or live per LLM_MODE).
4. Guards.
5. Persist and emit.
6. Recompute the stage and hand back the next rail set.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agent import guards, stub
from app.config import settings
from app.db import utcnow
from app.events import emit
from app.models import Conversation, Message, Rail

log = logging.getLogger("liner.agent")


def rails_for(db: Session, convo: Conversation) -> list[Rail]:
    """A lookup on (kind, stage, enabled) -- deterministic, seedable, testable.

    Deliberately not model-generated: chips are a second place a model could
    invent a car that isn't there, and they would add latency to every turn.
    """
    if convo.stage == "opening":
        followups = (
            db.query(Rail)
            .filter_by(kind="opener", enabled=True)
            .order_by(Rail.sort_order.asc())
            .all()
        )
    else:
        query = db.query(Rail).filter_by(kind="followup", stage=convo.stage, enabled=True)
        if not convo.focus_vehicle_id:
            query = query.filter(Rail.requires_vehicle.is_(False))
        followups = query.order_by(Rail.sort_order.asc()).limit(3).all()

    knowledge = (
        db.query(Rail)
        .filter_by(kind="knowledge", enabled=True)
        .order_by(Rail.sort_order.asc())
        .limit(2)
        .all()
    )
    return followups + knowledge


def record_buyer_message(
    db: Session, convo: Conversation, text: str, rail_id: str | None = None
) -> Message:
    message = Message(
        conversation_id=convo.id, role="buyer", content=text.strip(), via_rail_id=rail_id
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    emit(db, "conversation.message", {
        "conversation_id": convo.id, "message_id": message.id, "role": "buyer",
        "via_rail_id": rail_id,
    })
    return message


def run_agent_turn(db: Session, convo: Conversation, text: str) -> Message | None:
    """Returns the assistant message, or None when the agent is held."""
    if convo.agent_paused:
        # Liner is holding this conversation -- it will not reply again until
        # someone takes over or hands it back.
        return None

    if settings.llm_mode == "live":
        from app.agent import loop

        reply, calls = loop.run_turn(db, convo, text)
    else:
        reply, calls = stub.run_turn(db, convo, text)

        # Guards run in every mode. If a stubbed turn can slip an unsourced
        # price through, that is a hole in the guard and it should fail here
        # rather than in front of a prospect.
        assistant_turns = (
            db.query(Message).filter_by(conversation_id=convo.id, role="assistant").count()
        )
        verdict = guards.run_guards(
            reply,
            [c["result"] for c in calls],
            channel=convo.channel,
            attempt=2,  # the stub has no retry: it is deterministic
            assistant_turns=assistant_turns,
            booked=convo.stage == "booked",
        )
        if not verdict.ok:
            log.error(
                "stub turn failed guards on conversation %s: %s -- this is a guard or "
                "template bug, not a model problem", convo.id, verdict.violations,
            )
            reply = verdict.text

    message = Message(
        conversation_id=convo.id,
        role="assistant",
        content=reply,
        tool_calls_json=json.dumps(calls, default=str),
    )
    db.add(message)

    if convo.stage == "booked":
        convo.status = "closed"
        convo.ended_at = utcnow()
    convo.summary = reply[:200]
    db.commit()
    db.refresh(message)

    emit(db, "conversation.message", {
        "conversation_id": convo.id, "message_id": message.id, "role": "assistant",
        "stage": convo.stage,
        "tools": [c["name"] for c in calls],
    })
    return message
