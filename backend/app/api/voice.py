"""Voice: session mint and tool relay.

The architecture is real: the browser asks for an ephemeral provider token,
connects to the provider directly for audio (never proxied through us -- that
is latency we cannot afford), and relays tool calls back to /api/voice/tools,
which runs the same executors as chat and emits the same events.

What is missing is a provider. No vendor has been selected yet (§9 spike), so
``/sessions`` returns a typed not_configured error naming the missing keys and
``/call`` renders that state. There is deliberately no fake provider pushing a
scripted transcript: the flow would look like it worked while proving nothing
about latency, barge-in or audio quality, which are the only questions a real
provider answers.

The tool relay below is fully functional and channel-agnostic -- point a real
provider at it and Act 3 works.
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
from app.models import Conversation, Dealership, Message
from app.schemas.serialize import message_out

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/sessions")
def mint_session(
    db: Session = Depends(get_db), dealership: Dealership = Depends(get_dealership)
) -> dict:
    provider = get_voice_provider()
    instructions = build_system_prompt(db, dealership, live_settings(db))
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
    return message_out(message)


@router.post("/sessions/{conversation_id}/end")
def end_call(conversation_id: str, db: Session = Depends(get_db)) -> dict:
    convo = db.query(Conversation).filter_by(id=conversation_id).one_or_none()
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    convo.status = "closed"
    db.commit()
    emit(db, "call.ended", {"conversation_id": convo.id})
    return {"ok": True}
