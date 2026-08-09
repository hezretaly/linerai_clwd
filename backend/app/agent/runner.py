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
            tool_inputs=[c["input"] for c in calls if isinstance(c.get("input"), dict)],
            buyer_text=text,
        )
        if not verdict.ok:
            log.error(
                "stub turn failed guards on conversation %s: %s -- this is a guard or "
                "template bug, not a model problem", convo.id, verdict.violations,
            )
            reply = verdict.text

    return record_assistant_message(db, convo, reply, calls)


def _summarise(reply: str) -> str:
    """The thread's one-line summary, cut at a word.

    This is the last thing Liner said until the buyer ends the conversation,
    at which point close_conversation writes a real one. Slicing at 200
    characters cut mid-word -- "the Mazda3 -- it's" -- which reads as broken
    text rather than as a truncated sentence, and it is shown to a rep on the
    conversation rail.
    """
    text = " ".join((reply or "").split())
    if len(text) <= 200:
        return text
    cut = text[:200].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{cut}..."


def record_assistant_message(
    db: Session, convo: Conversation, reply: str, calls: list[dict]
) -> Message:
    """Persist one assistant turn and tell the dashboard about it.

    Shared with the booking form, which books through the executor rather than
    the model. Same row, same close-on-booked rule, same event -- a second copy
    of this is how a conversation ends up booked but still showing as open.
    """
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
    # close_conversation runs during this same turn and writes a real summary.
    # Overwriting it with the sign-off line ("Take care -- we're here when you
    # need us") threw away the one summary in the system that was actually a
    # summary, on every conversation that ended properly.
    if not any(c["name"] == "close_conversation" for c in calls):
        convo.summary = _summarise(reply)
    db.commit()
    db.refresh(message)

    emit(db, "conversation.message", {
        "conversation_id": convo.id, "message_id": message.id, "role": "assistant",
        "stage": convo.stage,
        "tools": [c["name"] for c in calls],
    })
    return message
