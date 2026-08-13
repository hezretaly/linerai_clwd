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

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent import tools
from app.agent.runner import record_buyer_message
from app.api.deps import current_user, get_dealership
from app.api.settings import live_settings
from app.agent.prompts import build_system_prompt
from app.db import get_db, utcnow
from app.events import emit
from app.integrations.registry import get_voice_provider
from app.config import settings
from app.integrations.voice.openai_realtime import CALLS_URL, price_of, rates_for
from app.models import (
    CallRecording,
    CallUsage,
    Conversation,
    Dealership,
    Message,
    User,
)
from app.schemas.serialize import iso, message_out

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
        # What the buyer hears on connect, before the microphone opens.
        #
        # Played by the browser, not spoken by the model. Asking a model to
        # greet is asking it to improvise an opening, and a smaller one
        # improvises the *customer's* -- a real call began "Hi! I'm looking for
        # a compact SUV" in the assistant's voice and then answered itself. A
        # pre-roll is the same words every time, cannot be interrupted by the
        # connection settling, and costs no output audio tokens.
        #
        # Empty means the browser plays a tone. The text is sent alongside so
        # whoever records the real thing knows what it should say.
        "greeting_audio": settings.voice_greeting_audio,
        "greeting": live_settings(db).greeting,
        # Whether the buyer's own words will be written down. Off, the dealer's
        # transcript is Liner's half only -- which reads exactly like an
        # assistant talking to itself, and is the single most confusing thing
        # this page can show without saying why.
        "transcribed": settings.voice_transcribe,
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
        if _is_noise(body.content):
            return {"recorded": False, "reason": "non-verbal"}
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


def _is_noise(text: str) -> bool:
    """A grunt the transcriber gave a spelling to, rather than something said.

    A real call produced a buyer message reading `嗯` -- a Chinese filler
    particle, from someone speaking English who had made an "mm" sound. Two
    things went wrong and only one of them is here: the session now names the
    language, which stops the transcriber reaching for another one. But even
    transcribed as "Mm", that is not a thing the buyer said, and it does not
    belong in the record a rep reads before phoning them back.

    Deliberately narrow. Three characters or fewer, with no letter or digit
    that this assistant could have been speaking -- it is an English-only
    channel, and the prompt says so. Anything longer, or anything containing a
    word, is kept whatever it looks like: dropping a message a buyer really
    sent is far worse than keeping one they did not.
    """
    stripped = (text or "").strip(" .,!?-–—…\"'")
    if not stripped:
        return True
    return len(stripped) <= 3 and not any(c.isascii() and c.isalnum() for c in stripped)


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


class Usage(BaseModel):
    conversation_id: str
    response_id: str = ""
    usage: dict = {}


@router.post("/usage")
def record_usage(body: Usage, db: Session = Depends(get_db)) -> dict:
    """What one response on this call actually cost, in tokens.

    Relayed from the browser because that is where the numbers are: the
    provider reports them on `response.done`, over a data channel this server
    is not part of. Recorded rather than estimated from wall-clock, and that
    distinction is the whole point -- "about twenty-five cents a minute" is not
    a number anyone can act on, while "the eleventh turn cost six times the
    second, and caching stopped hitting at turn four" is.

    Unauthenticated for the same reason `/voice/tools` is: it is the buyer's
    browser talking, and a buyer has no session. It writes token counts against
    a conversation that must already exist, and nothing else.
    """
    convo = db.query(Conversation).filter_by(id=body.conversation_id).one_or_none()
    if convo is None:
        raise HTTPException(404, "Conversation not found")

    # Idempotent on the provider's response id. A relay that retries must not
    # double a call's apparent cost -- a cost report nobody trusts is one
    # nobody reads.
    if body.response_id:
        seen = (
            db.query(CallUsage)
            .filter_by(conversation_id=convo.id, response_id=body.response_id)
            .first()
        )
        if seen is not None:
            return {"recorded": False, "reason": "duplicate"}

    data = body.usage or {}
    into = data.get("input_token_details") or {}
    out = data.get("output_token_details") or {}
    cached = into.get("cached_tokens_details") or {}

    # Cached tokens are *included* in the audio and text counts the provider
    # reports, so charging both would double-count the discount away. Fresh
    # input is what is left after taking the cached part out.
    cached_total = int(into.get("cached_tokens") or 0)
    cached_audio = int(cached.get("audio_tokens") or 0)
    cached_text = int(cached.get("text_tokens") or 0)

    row = CallUsage(
        conversation_id=convo.id,
        response_id=body.response_id,
        model=settings.voice_model,
        input_tokens=int(data.get("input_tokens") or 0),
        input_audio_tokens=max(int(into.get("audio_tokens") or 0) - cached_audio, 0),
        input_text_tokens=max(int(into.get("text_tokens") or 0) - cached_text, 0),
        cached_tokens=cached_total,
        cached_audio_tokens=cached_audio,
        output_tokens=int(data.get("output_tokens") or 0),
        output_audio_tokens=int(out.get("audio_tokens") or 0),
        output_text_tokens=int(out.get("text_tokens") or 0),
    )
    db.add(row)
    db.commit()
    return {"recorded": True, "estimated_usd": round(price_of(_as_dict(row), row.model), 6)}


def _as_dict(row: CallUsage) -> dict:
    return {
        "cached_tokens": row.cached_tokens,
        "input_audio_tokens": row.input_audio_tokens,
        "input_text_tokens": row.input_text_tokens,
        "output_audio_tokens": row.output_audio_tokens,
        "output_text_tokens": row.output_text_tokens,
    }


@router.get("/recordings")
def list_recordings(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Every recording and the file it points at.

    Exists so the gate can assert the one thing that is invisible from any
    single call: that two calls do not share a file. They did, for two commits.
    """
    rows = db.query(CallRecording).order_by(CallRecording.created_at.desc()).limit(200).all()
    return {
        "count": len(rows),
        "recordings": [
            {
                "conversation_id": r.conversation_id,
                "filename": r.filename,
                "size_bytes": r.size_bytes,
                "duration_ms": r.duration_ms,
                "complete": bool(r.duration_ms),
                # Quarantined rather than deleted: the row is evidence a call
                # happened, and the file it names is somebody's audio -- just
                # not knowably this buyer's.
                "orphaned": _orphaned(r),
            }
            for r in rows
        ],
    }


@router.get("/cost/{conversation_id}")
def call_cost(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """What a call cost, turn by turn.

    Turn by turn rather than a total, because the total hides the shape and the
    shape is the finding: a realtime call bills the whole conversation so far
    as input on *every* turn, so the eleventh costs several times the second
    with nothing else different. A single number would leave someone shortening
    their prompt when the fix is capping the history.

    `estimated_usd` is exactly that -- the token counts are the provider's own,
    the rates are configuration that can go stale, and the vendor's billing
    page is the authority.
    """
    rows = (
        db.query(CallUsage)
        .filter_by(conversation_id=conversation_id)
        .order_by(CallUsage.created_at.asc())
        .all()
    )
    turns = [
        {
            "at": iso(r.created_at),
            "input_tokens": r.input_tokens,
            "cached_tokens": r.cached_tokens,
            "fresh_input_tokens": r.input_audio_tokens + r.input_text_tokens,
            "output_tokens": r.output_tokens,
            "output_audio_tokens": r.output_audio_tokens,
            "estimated_usd": round(price_of(_as_dict(r), r.model), 6),
        }
        for r in rows
    ]
    cached = sum(r.cached_tokens for r in rows)
    billed_in = sum(r.input_audio_tokens + r.input_text_tokens for r in rows)
    model = rows[0].model if rows else settings.voice_model
    _, priced = rates_for(model)
    return {
        "conversation_id": conversation_id,
        "turns": turns,
        "responses": len(turns),
        "estimated_usd": round(sum(t["estimated_usd"] for t in turns), 6),
        # The single most useful number here. Cached input is discounted by
        # roughly eighty times, so a call where caching stopped hitting costs
        # several times one where it did -- and nothing else about the two
        # calls looks any different.
        "cache_hit_ratio": round(cached / (cached + billed_in), 3) if cached + billed_in else 0.0,
        "model": model,
        # Whether the money means anything at all. An unknown model is reported
        # as unpriced rather than charged at some other model's rates: a cost
        # report that is confidently wrong is worse than one that says it does
        # not know.
        "priced": priced,
        "note": (
            f"Estimated from the token counts the provider reported for {model}, at "
            "the published rates. VOICE_PRICE_* overrides them; the vendor's billing "
            "page is the authority."
            if priced else
            f"No published rates are known for {model}, so these calls are not "
            "priced. Set VOICE_PRICE_AUDIO_IN and the rest to value them."
        ),
    }


@router.post("/sessions/{conversation_id}/end")
def end_call(conversation_id: str, db: Session = Depends(get_db)) -> dict:
    convo = db.query(Conversation).filter_by(id=conversation_id).one_or_none()
    if convo is None:
        raise HTTPException(404, "Conversation not found")
    convo.status = "closed"
    # Stamped here as well as in close_conversation. Without it a call that the
    # buyer simply hung up on had no end time at all, so its length was
    # unknowable -- and "how long was that call?" is the first thing anyone
    # asks about a phone bill.
    if convo.ended_at is None:
        convo.ended_at = utcnow()
    db.commit()
    emit(db, "call.ended", {
        "conversation_id": convo.id,
        "seconds": int((convo.ended_at - convo.started_at).total_seconds()),
    })
    return {"ok": True, "seconds": int((convo.ended_at - convo.started_at).total_seconds())}


# --------------------------------------------------------------------------
# The audio itself.
#
# A recorded call is a buyer's voice, so two things are load-bearing and
# neither is technical: the buyer is told before the microphone opens (the
# call page says so, above the button), and the file never leaves this server
# without a dealer session. Several US states require every party to consent
# to a recording, which is a decision for the dealership to take with its own
# counsel -- what this code guarantees is that nobody is recorded silently.
# --------------------------------------------------------------------------

#: Ten minutes of Opus at a sane bitrate, with room to spare. A cap is not
#: optional on an endpoint no session guards.
MAX_RECORDING = 40 * 1024 * 1024

#: What a browser will actually produce. Safari's MediaRecorder emits mp4 and
#: Chrome's emits webm, so both are here -- and nothing else is, because this
#: writes a file to disk from an unauthenticated request.
AUDIO_TYPES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
}


def recordings_dir():
    from pathlib import Path

    from app.config import BACKEND_DIR

    path = Path(BACKEND_DIR) / "var" / "recordings"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _open_recording(db: Session, conversation_id: str, content_type: str) -> CallRecording:
    """The row for this call's audio, made on the first chunk."""
    convo = db.query(Conversation).filter_by(id=conversation_id).one_or_none()
    if convo is None or convo.channel != "voice":
        raise HTTPException(404, "No such call")

    row = db.query(CallRecording).filter_by(conversation_id=conversation_id).one_or_none()
    if row is not None and not _orphaned(row):
        return row

    suffix = AUDIO_TYPES.get((content_type or "").split(";")[0].strip())
    if suffix is None:
        raise HTTPException(415, f"Unsupported audio type {content_type!r}")
    if row is not None:
        # Written by the version that read `id` before it existed. Its file is
        # shared with every other call recorded then, so it is not this call's
        # audio and must not be served as though it were.
        db.delete(row)
        db.flush()
    row = CallRecording(
        conversation_id=conversation_id,
        content_type=(content_type or "").split(";")[0].strip(),
        size_bytes=0,
        # Zero until the call says it is finished. That is the only marker of a
        # complete recording -- adding a boolean column would need a migration
        # this codebase deliberately does not have, and a length is a thing a
        # half-written file genuinely does not yet know.
        duration_ms=0,
    )
    # Flushed before the filename is built, because `id` is generated by the
    # column default at flush time -- read any earlier it is None, and every
    # call on the system writes to a file called "None.webm". That shipped:
    # each recording overwrote the last, and it only looked fine because a
    # single upload per call meant the last writer won.
    db.add(row)
    db.flush()
    row.filename = f"{row.id}{suffix}"
    (recordings_dir() / row.filename).write_bytes(b"")
    db.commit()
    return row


@router.post("/recording/{conversation_id}/chunk")
async def append_chunk(
    conversation_id: str,
    seq: int = 0,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    """One slice of a call, appended as it is recorded.

    Streamed rather than uploaded at the end, because the end is the least
    reliable moment there is: a closed tab, a crashed browser or a dropped
    connection all skip it, and a call whose audio only exists in a page that
    has gone is a call with no audio. Everything up to the last slice is
    already on disk here.

    **The client must send these in order and one at a time.** A webm or mp4
    written by MediaRecorder is a header followed by continuation clusters;
    reorder them and the file is unplayable. `seq` is carried for the log
    rather than enforced -- tracking the expected next value would need a
    column, and the ordering guarantee belongs where the ordering is: the
    single promise chain in the browser that sends these.
    """
    row = _open_recording(db, conversation_id, file.content_type or "")
    path = recordings_dir() / row.filename

    raw = await file.read(MAX_RECORDING + 1)
    if not raw:
        return {"stored": True, "bytes": row.size_bytes}
    if row.size_bytes + len(raw) > MAX_RECORDING:
        # A cap on an endpoint no session guards is not optional. Silently
        # keeping what fits beats refusing the whole call.
        return {"stored": False, "reason": "full", "bytes": row.size_bytes}

    with path.open("ab") as handle:
        handle.write(raw)
    row.size_bytes = row.size_bytes + len(raw)
    db.commit()
    return {"stored": True, "seq": seq, "bytes": row.size_bytes}


@router.post("/recording/{conversation_id}/complete")
def complete_recording(
    conversation_id: str,
    duration_ms: int = 0,
    db: Session = Depends(get_db),
) -> dict:
    """That was the end of the call.

    Pressing the red button, the model closing the conversation, the idle
    timeout and a closed tab all land here. Until it is called the row carries
    `duration_ms = 0`, which is what "still being written, or abandoned" looks
    like -- and the dashboard says so rather than offering a player for a file
    that may be half a call.
    """
    row = db.query(CallRecording).filter_by(conversation_id=conversation_id).one_or_none()
    if row is None:
        return {"complete": False, "reason": "nothing was recorded"}
    if not row.duration_ms:
        row.duration_ms = max(duration_ms, 1)
        db.commit()
    return {"complete": True, "bytes": row.size_bytes, "duration_ms": row.duration_ms}


def _orphaned(row: CallRecording) -> bool:
    """A row from before the filename was built after the flush.

    `id` is generated by the column default at flush time; read a moment
    earlier it is None, so every recording made then landed in one file called
    `None.webm`. Those rows do not point at their own call's audio -- they
    point at everybody's -- which is worse than pointing at nothing.
    """
    return not row.filename or row.filename.startswith("None.")


@router.get("/recording/{conversation_id}")
def fetch_recording(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Play a call back. Dealer session required -- it is somebody's voice."""
    row = db.query(CallRecording).filter_by(conversation_id=conversation_id).one_or_none()
    if row is None:
        raise HTTPException(404, "No recording for this call")
    if _orphaned(row):
        raise HTTPException(
            410,
            "This recording was written to a shared file by an earlier version and "
            "is not this call's audio. It has not been served.",
        )
    path = recordings_dir() / row.filename
    if not path.exists():
        # The row outlived the file -- a restored database, a cleared volume.
        # Saying so beats a broken player with no explanation.
        raise HTTPException(410, "The audio for this call is no longer on disk")
    return FileResponse(path, media_type=row.content_type, filename=row.filename)
