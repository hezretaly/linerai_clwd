"""Voice: session mint and tool relay.

The architecture is real: the browser asks for an ephemeral provider token,
connects to the provider directly for audio (never proxied through us -- that
is latency we cannot afford), and relays tool calls back to /api/voice/tools,
which runs the same executors as chat and emits the same events.

The provider is OpenAI Realtime (``VOICE_PROVIDER=openai``). Without it, or
without a key, ``/sessions`` returns a typed not_configured error naming what
is missing and ``/call`` renders that state. There is deliberately no fake
provider pushing a scripted transcript: it would look like it worked while
proving nothing about latency, barge-in or audio quality, which are the only
questions a real provider answers.

**The reply guard cannot gate a call.** In chat a draft that quotes an
unsourced price is discarded before the buyer sees it. Here the audio never
passes through this server, so the words are spoken before we have them. The
guard therefore runs on the transcript on its way in -- after the fact, raising
a handoff it cannot un-speak. Everything enforced inside an executor is
unaffected: a do-not-discuss vehicle is filtered in ``search_inventory``, a
clash is refused in ``book_appointment``, provenance is downgraded in
``save_captured_fields``. Those hold on a call exactly as they do in chat,
which is the whole reason the rules live in executors.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent import tools
from app.agent.runner import record_buyer_message
from app.api.deps import get_dealership
from app.api.settings import live_settings
from app.agent.prompts import build_system_prompt
from app.db import get_db
from app.events import emit
from app.integrations.registry import get_voice_provider
from app.integrations.voice.openai_realtime import CALLS_URL
from app.models import Conversation, Dealership, Message
from app.schemas.serialize import message_out

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/sessions")
def mint_session(
    db: Session = Depends(get_db), dealership: Dealership = Depends(get_dealership)
) -> dict:
    provider = get_voice_provider()
    # channel="voice" appends the rules that only make sense out loud: no
    # markdown, no read-out lists, no booking card, numbers said the way people
    # say them. Appended to the same prompt rather than a second one, so a
    # policy change lands on both channels or neither.
    instructions = build_system_prompt(db, dealership, live_settings(db), channel="voice")
    # Raises NotConfigured -> 503 with the missing keys named. The call UI
    # reads that payload and says exactly what is absent.
    session = provider.mint_session(instructions, tools.TOOL_DEFS)

    convo = Conversation(channel="voice", status="active", stage="opening")
    db.add(convo)
    db.commit()
    db.refresh(convo)
    emit(db, "call.started", {"conversation_id": convo.id})
    return {
        "conversation_id": convo.id,
        "provider": session.provider,
        "client_secret": session.client_secret,
        "expires_in": session.expires_in,
        # The browser talks audio straight to the vendor, so it needs to know
        # where. Sent from here rather than hardcoded in the frontend: a
        # compatible or proxied endpoint then becomes a server setting instead
        # of a rebuild.
        "calls_url": CALLS_URL,
        "model": session.model,
    }


class ToolRelay(BaseModel):
    conversation_id: str
    name: str
    input: dict
    tool_call_id: str | None = None


@router.post("/tools")
def relay_tool(body: ToolRelay, db: Session = Depends(get_db)) -> dict:
    """Executed for tool calls the browser relays off the provider's data
    channel. Same executors as chat, same rows, same events."""
    convo = db.query(Conversation).filter_by(id=body.conversation_id).one_or_none()
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    try:
        return {"result": tools.execute(db, convo, body.name, body.input, body.tool_call_id)}
    except tools.ToolError as exc:
        return {"error": str(exc)}


class TranscriptChunk(BaseModel):
    conversation_id: str
    role: str
    content: str


@router.post("/transcript")
def append_transcript(body: TranscriptChunk, db: Session = Depends(get_db)) -> dict:
    """Transcript chunks land in `messages` like any other turn, so a voice
    call reads the same as a chat in the dealer's transcript view."""
    convo = db.query(Conversation).filter_by(id=body.conversation_id).one_or_none()
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    if body.role not in {"buyer", "assistant"}:
        raise HTTPException(400, "role must be 'buyer' or 'assistant'")

    if body.role == "buyer":
        return message_out(record_buyer_message(db, convo, body.content))

    message = Message(conversation_id=convo.id, role="assistant", content=body.content)
    db.add(message)
    db.commit()
    db.refresh(message)
    emit(db, "conversation.message", {
        "conversation_id": convo.id, "message_id": message.id, "role": "assistant",
    })
    flagged = _guard_after_the_fact(db, convo, body.content)
    return {**message_out(message), "guard_violations": flagged}


def _guard_after_the_fact(db: Session, convo: Conversation, spoken: str) -> list[str]:
    """Run the reply guard on what was already said, and raise a person if it
    fails.

    In chat the guard is a gate: a draft quoting an unsourced price is thrown
    away and never reaches the buyer. On a call it cannot be. Audio goes
    browser-to-vendor with no server in the path -- that is the whole reason a
    call does not sound like a hold queue -- so by the time these words arrive
    here they are already in someone's ear.

    Running it anyway is not theatre. It cannot unsay a number, and this is
    written down rather than implied; what it can do is tell a rep that Liner
    quoted something it had not looked up, which is the difference between a
    caller who gets a correction and one who turns up expecting a price nobody
    will honour. The executors are untouched by any of this: a do-not-discuss
    car never reaches the model, and a clash is still refused at booking.
    """
    from app.agent import guards

    sourced = [
        result
        for message in db.query(Message)
        .filter(Message.conversation_id == convo.id, Message.tool_calls_json.is_not(None))
        .all()
        for result in guards.tool_results_from_messages(message.tool_calls_json or "[]")
    ]
    buyer_said = " ".join(
        m.content or ""
        for m in db.query(Message)
        .filter(Message.conversation_id == convo.id, Message.role == "buyer")
        .all()
    )
    violations = guards.check_unsourced_facts(spoken, sourced, buyer_text=buyer_said)
    if violations:
        try:
            # The same executor the chat loop uses, so this lands in the same
            # queue and obeys the same one-open-handoff rule -- a call that
            # trips the guard on four turns is one job for a rep, not four.
            tools.execute(db, convo, "escalate_to_human", {
                "rule_key": "asks_for_manager",
                "reason": "Liner said something on this call it had not looked up: "
                          + "; ".join(violations),
            }, f"voice-guard-{convo.id}")
        except tools.ToolError:
            pass
    return violations


@router.post("/sessions/{conversation_id}/end")
def end_call(conversation_id: str, db: Session = Depends(get_db)) -> dict:
    convo = db.query(Conversation).filter_by(id=conversation_id).one_or_none()
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    convo.status = "closed"
    db.commit()
    emit(db, "call.ended", {"conversation_id": convo.id})
    return {"ok": True}
