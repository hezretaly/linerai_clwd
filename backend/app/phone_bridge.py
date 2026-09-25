"""The bridge: Twilio Media Streams on one side, OpenAI Realtime on the other.

# PLACEHOLDER(twilio-bridge): this has never run end to end. There is no
# `OPENAI_API_KEY` here, `api.openai.com` is refused by the egress proxy, and
# no Twilio account has ever opened this socket. What is real and exercised by
# `make smoke` is everything that does not need either: the frame each side is
# sent, the barge-in ordering, the tool dispatch, and the fact that a socket
# with no model configured closes with a reason instead of hanging.

**This is the one channel where the server is in the audio path**, and that is
forced rather than chosen. On `/call` the browser talks WebRTC straight to
OpenAI because a round trip through us is latency a conversation cannot
afford. A telephone call has no browser: Twilio opens a WebSocket to *this*
process, so this process holds the other end.

One good thing falls out of it. On `/call` the tool calls come back over a data
channel and have to be relayed to `/api/voice/tools`; here they arrive in the
same process that owns the database session, so they are executed directly. The
executors are the same ones chat uses either way -- that is what makes a do-not-
discuss vehicle stay filtered and a double booking stay refused, whatever the
model says.

**No transcoding, anywhere.** Twilio sends base64 G.711 mu-law at 8kHz and
takes it back in the same shape; the Realtime session is told `audio/pcmu`, so
the payload is passed through byte for byte. The alternative -- mu-law to PCM16,
8k to 24k and back -- is a resample per frame per call, and it would lean on
`audioop`, which Python removed in 3.13.

**Barge-in has an order and it matters.** When the caller starts talking the
provider is told to stop generating *and* Twilio is told to drop what it has
already buffered. Cancelling alone leaves up to a few seconds of audio queued
at Twilio, which the caller hears as the assistant talking over them after they
interrupted -- the same failure `/call` had, arriving by a different route.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app import flags, phone_persona
from app.config import settings
from app.db import SessionLocal, ops_session, utcnow
from app.events import emit_ops
from app.models import Conversation, Message, PhoneCall

log = logging.getLogger("liner.phone.bridge")

router = APIRouter()

#: Close codes. 1011 is "server error" and is what a caller's carrier reports;
#: the reason string is for our logs, since nothing on a phone can read it.
UNCONFIGURED = 1011


# --------------------------------------------------------------------------
# The frames, as functions
# --------------------------------------------------------------------------
# Pulled out of the loops rather than written inline, for one reason: they are
# the only part of this file that can be checked without a Twilio account and
# an OpenAI key. A frame with a misspelled key is silently ignored by the other
# end -- Twilio drops an unknown event, the provider drops an unknown type --
# so the failure is a call with no audio and nothing in any log. `make smoke`
# asserts these, which is the difference between "it compiled" and "the bytes
# are right".

def to_model(payload: str) -> str:
    """Caller audio, on its way to the provider. Base64 mu-law, untouched."""
    return json.dumps({"type": "input_audio_buffer.append", "audio": payload})


def to_caller(stream_sid: str, payload: str) -> str:
    """Model audio, on its way back to Twilio.

    `streamSid` is on every frame: Twilio multiplexes streams over one
    connection, and a frame without it is discarded with no error.
    """
    return json.dumps({
        "event": "media",
        "streamSid": stream_sid,
        "media": {"payload": payload},
    })


def clear_caller(stream_sid: str) -> str:
    """Drop whatever Twilio still has buffered. The second half of a barge-in."""
    return json.dumps({"event": "clear", "streamSid": stream_sid})


def cancel_model() -> str:
    """Stop generating. The first half of a barge-in."""
    return json.dumps({"type": "response.cancel"})


def tool_result(call_id: str, output: dict) -> str:
    return json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(output, default=str),
        },
    })


def ask_for_reply() -> str:
    return json.dumps({"type": "response.create"})


@router.websocket("/ws/phone/media")
async def media_socket(websocket: WebSocket, call: str = Query("")) -> None:
    """Twilio dialled us. Sit between it and the model until somebody hangs up.

    `call` is the row id, handed over in the stream URL by
    `api/phone/incoming` -- so the socket knows whose call this is without
    trusting the socket to say, and without a second lookup by SID.
    """
    await websocket.accept()

    db = SessionLocal()
    # `ops_phone_calls` is Liner's own: one number, one log, never per store.
    # The row is written through `ops`, the session it came from. It was
    # `db.commit()`, which flushes only the store's session, so a call that
    # could not be answered was never marked failed; and `ops` was left open
    # across the whole call below.
    ops = ops_session()
    try:
        row = ops.query(PhoneCall).filter_by(id=call).one_or_none() if call else None
        if row is None:
            log.warning("media socket opened for unknown call %r", call)
            await websocket.close(code=UNCONFIGURED, reason="Unknown call")
            return
        persona = row.persona or flags.PHONE_LINER
        try:
            instructions, tools, convo = _brief_for(db, row, persona)
        except NotConfiguredHere as exc:
            log.error("cannot answer call %s: %s", row.id, exc)
            row.status = "failed"
            row.ended_at = utcnow()
            ops.commit()
            await websocket.close(code=UNCONFIGURED, reason=str(exc)[:110])
            return
        # The dealership persona puts its new conversation's id on the row.
        ops.commit()
    finally:
        db.close()
        ops.close()

    await _pump(websocket, call_id=call, persona=persona,
                instructions=instructions, tools=tools, conversation_id=convo)


class NotConfiguredHere(Exception):
    """Something the call needs is absent. Closes the socket with a reason."""


def _brief_for(db, row: PhoneCall, persona: str) -> tuple[str, list[dict], str]:
    """Who answers, what they may do, and the thread it is written into.

    The two personas differ in every one of those three, which is the whole
    point of the switch -- and why this returns all three together rather than
    letting a caller assemble a prompt from one persona with another's tools.
    """
    from app.integrations.voice.openai_realtime import api_key

    if not api_key():
        raise NotConfiguredHere("OPENAI_API_KEY is not set, so nobody can answer.")

    if persona == flags.PHONE_DEALERSHIP:
        # The buyer-facing assistant, unchanged: the same prompt `/call` builds,
        # the same nine tools, the same executors. A second prompt for the
        # phone is how the price rule ends up stricter on one channel.
        from app.agent import tools as agent_tools
        from app.agent.prompts import build_system_prompt
        from app.api.settings import live_settings
        from app.models import Dealership

        dealership = db.query(Dealership).first()
        if dealership is None:
            raise NotConfiguredHere("No dealership row. Run `make seed`.")
        convo = Conversation(channel="voice", status="active", stage="opening")
        db.add(convo)
        db.commit()
        db.refresh(convo)
        row.conversation_id = convo.id
        db.commit()
        return (
            build_system_prompt(db, dealership, live_settings(db), channel="voice"),
            agent_tools.TOOL_DEFS,
            convo.id,
        )

    # Liner's own. Deliberately no dealership tools at all -- see
    # `app/phone_persona.py`: the realm split is enforced by what the model is
    # handed, not by asking it not to look.
    return phone_persona.instructions(), phone_persona.TOOL_DEFS, ""


async def _pump(
    websocket: WebSocket,
    *,
    call_id: str,
    persona: str,
    instructions: str,
    tools: list[dict],
    conversation_id: str,
) -> None:
    """Both directions at once, until either side goes away."""
    import websockets

    from app.integrations.voice.openai_realtime import (
        PHONE_AUDIO,
        SOCKET_URL,
        OpenAIRealtimeProvider,
        api_key,
    )

    provider = OpenAIRealtimeProvider()
    session = provider.session_payload(instructions, tools, None, PHONE_AUDIO)["session"]
    # The socket carries the session in an update frame rather than in the URL,
    # so this is the same dict the WebRTC path posts -- one payload builder,
    # and a policy change lands on both channels or neither.
    url = f"{SOCKET_URL}?model={settings.voice_model}"

    state = _Call(call_id=call_id, persona=persona, conversation_id=conversation_id)

    try:
        async with websockets.connect(
            url, additional_headers={"Authorization": f"Bearer {api_key()}"}
        ) as model:
            await model.send(json.dumps({"type": "session.update", "session": session}))
            await asyncio.gather(
                _from_caller(websocket, model, state),
                _from_model(websocket, model, state),
            )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # a refused key, a dropped provider, a bad model
        log.exception("phone bridge failed on call %s", call_id)
        with ops_session() as ops:
            row = ops.query(PhoneCall).filter_by(id=call_id).one_or_none()
            if row is not None:
                row.status = row.status if row.status == "completed" else "failed"
                row.ended_at = row.ended_at or utcnow()
                ops.commit()
        try:
            await websocket.close(code=UNCONFIGURED, reason=str(exc)[:110])
        except RuntimeError:
            pass
    finally:
        _finish(call_id)


class _Call:
    """What the two directions need to say to each other.

    A small object rather than nonlocals, because both coroutines mutate it and
    a closure over three flags is exactly where a barge-in race hides.
    """

    def __init__(self, call_id: str, persona: str, conversation_id: str) -> None:
        self.call_id = call_id
        self.persona = persona
        self.conversation_id = conversation_id
        #: Twilio's handle for this stream. Every frame sent back names it.
        self.stream_sid = ""
        #: Set when a tool says the call is over, so the goodbye is allowed to
        #: finish and then the line goes down -- the same rule `/call` follows,
        #: and the same reason: saying goodbye is not hanging up.
        self.closing = False
        self.done = asyncio.Event()


@contextmanager
def _session():
    """A short database session, closed whatever happens.

    The bridge is long-lived and a call can last ten minutes; holding one
    session open across it would pin a SQLite connection and read stale rows
    for the whole call. Each piece of work takes its own.

    The store's session only. The call row is Liner's own, so whatever
    touches it opens `ops_session()` beside this -- an ops session opened
    here was never handed to anybody, and the callers that meant to use it
    named an `ops` that did not exist in their scope.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def _from_caller(websocket: WebSocket, model, state: _Call) -> None:
    """Twilio -> the model. Audio, and the two events that bound a call."""
    try:
        while True:
            raw = await websocket.receive_text()
            event = json.loads(raw)
            kind = event.get("event")

            if kind == "start":
                start = event.get("start") or {}
                state.stream_sid = start.get("streamSid", "")
                log.info("media stream %s open for call %s", state.stream_sid, state.call_id)
            elif kind == "media":
                # Straight through. Twilio's payload is already base64 G.711
                # mu-law and the session was told to expect exactly that.
                await model.send(to_model((event.get("media") or {}).get("payload", "")))
            elif kind == "stop":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        state.done.set()
        try:
            await model.close()
        except Exception:
            pass


async def _from_model(websocket: WebSocket, model, state: _Call) -> None:
    """The model -> Twilio. Audio, barge-in, tool calls and the transcript."""
    try:
        async for raw in model:
            event = json.loads(raw)
            kind = event.get("type")

            if kind == "response.output_audio.delta":
                if state.stream_sid:
                    await websocket.send_text(
                        to_caller(state.stream_sid, event.get("delta", ""))
                    )

            elif kind == "input_audio_buffer.speech_started":
                # **Both halves, in this order.** Cancelling the response stops
                # the model generating; clearing Twilio's buffer drops what it
                # has already been handed. Without the second the caller hears
                # the assistant carry on for a second or two after they cut in,
                # which is the thing that makes a phone bot unbearable.
                await _cancel(model)
                if state.stream_sid:
                    await websocket.send_text(clear_caller(state.stream_sid))

            elif kind == "response.function_call_arguments.done":
                await _run_tool(model, state, event)

            elif kind == "response.output_audio_transcript.done":
                _remember(state, "assistant", event.get("transcript", ""))

            elif kind == "conversation.item.input_audio_transcription.completed":
                _remember(state, "buyer", event.get("transcript", ""))

            elif kind == "response.done":
                if state.closing:
                    # The goodbye has finished. Hanging up is Twilio's job, and
                    # closing this socket is what tells it to.
                    break

            elif kind == "error":
                log.error("realtime error on call %s: %s", state.call_id, event.get("error"))
    except Exception:
        log.exception("model side failed on call %s", state.call_id)
    finally:
        state.done.set()
        try:
            await websocket.close()
        except Exception:
            pass


async def _cancel(model) -> None:
    try:
        await model.send(cancel_model())
    except Exception:
        # A cancel that cannot be sent is a dropped provider connection, which
        # the read loop is about to notice anyway.
        pass


async def _run_tool(model, state: _Call, event: dict[str, Any]) -> None:
    """Execute it here, in the process that owns the database session.

    The `/call` path has to relay these to `/api/voice/tools` because the model
    is talking to a browser. Here there is no relay and no second entry point
    -- but it is the *same executors*, which is the part that matters: every
    rule that survives a channel with no server in the audio path survives this
    one for the identical reason.
    """
    name = event.get("name", "")
    call_id = event.get("call_id", "")
    try:
        args = json.loads(event.get("arguments") or "{}")
    except ValueError:
        args = {}

    output: dict[str, Any]
    try:
        output = await asyncio.to_thread(_execute, state, name, args)
    except Exception as exc:
        # Handed back as a result rather than swallowed. A model told the tool
        # errored asks the caller something else; a model told nothing waits.
        output = {"error": str(exc)}

    if output.get("closed"):
        state.closing = True

    await model.send(tool_result(call_id, output))
    await model.send(ask_for_reply())


def _execute(state: _Call, name: str, args: dict) -> dict:
    """Blocking DB work, off the event loop."""
    with _session() as db:
        if state.persona == flags.PHONE_DEALERSHIP:
            from app.agent import tools as agent_tools

            convo = db.query(Conversation).filter_by(id=state.conversation_id).one_or_none()
            if convo is None:
                return {"error": "This call has no conversation."}
            try:
                return agent_tools.execute(db, convo, name, args, f"phone-{state.call_id}-{name}")
            except agent_tools.ToolError as exc:
                return {"error": str(exc)}

        with ops_session() as ops:
            row = ops.query(PhoneCall).filter_by(id=state.call_id).one_or_none()
            if row is None:
                return {"error": "This call is no longer on file."}
            try:
                result = phone_persona.execute(db, row, name, args)
            except phone_persona.ToolError as exc:
                return {"error": str(exc)}
            # The executors stamp the call row (`end_call`, `book_demo`) and
            # commit the session they were handed, which is the store's. The
            # row is Liner's own, so it is written here, where it was read.
            ops.commit()
            return result


def _remember(state: _Call, role: str, text: str) -> None:
    """Write a line of the call down, where there is a thread to write it into.

    Only the dealership persona has one: that call is a buyer's conversation
    and belongs on their page like any other. Liner's own line writes a summary
    onto the call row instead -- a prospect ringing us is not a buyer, and
    minting a `Conversation` for them would put a dealership row behind
    somebody who was never in a showroom.
    """
    text = (text or "").strip()
    if not text or not state.conversation_id:
        return
    with _session() as db:
        db.add(Message(
            conversation_id=state.conversation_id,
            role="assistant" if role == "assistant" else "buyer",
            content=text,
        ))
        db.commit()


def _finish(call_id: str) -> None:
    """Stamp the row when the socket goes, whatever took it.

    Twilio's status callback does this properly with a duration; this is the
    backstop for a call whose callback never arrives -- a dropped webhook, a
    reseed, a second environment -- so a finished call is never left reading
    as in progress for ever.
    """
    with ops_session() as ops:
        row = ops.query(PhoneCall).filter_by(id=call_id).one_or_none()
        if row is None:
            return
        if row.ended_at is not None:
            # The status callback beat us to it and recorded a real duration.
            # Emitting again would put a second "call ended" on the dashboard
            # for one call.
            return
        row.ended_at = utcnow()
        row.status = row.status if row.status == "completed" else "in-progress"
        ops.commit()
        # Announced the way the status callback announces it (`api/phone.py`):
        # the call is ours, so its event goes through `emit_ops`.
        emit_ops("phone.ended", {"call_id": row.id, "status": row.status})
