"""The phone line: Twilio's webhooks, and Liner's own controls for it.

Three kinds of endpoint live here and they have three different doors, which
is the thing to keep straight:

* **`/api/phone/incoming`, `/status` and `/outbound-twiml` are Twilio's.** No
  session and no cookie -- a telephone network has neither. What stands in
  front of them is the `X-Twilio-Signature` HMAC, which covers both the body
  and the URL it was sent to. They are public URLs that will be found.
* **`/ws/phone/media` is the Media Streams socket**, and it lives in
  `app/phone_bridge.py` because it is a different kind of thing entirely.
* **`/api/ops/phone*` is ours**, behind `require_owner` like everything else
  under `/ops`. A dealership's manager is refused here exactly as a rep is
  refused from the rest of it.

**A refused webhook still answers in TwiML.** Returning a bare 403 to Twilio
puts an "application error" recording in the caller's ear; returning a spoken
sentence and a hangup at least tells a person something. The refusal is the
same either way -- nothing is opened, nothing is billed, no row is written.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import flags
from app.api.deps import require_owner
from app.config import settings
from app.db import get_db, utcnow
from app.events import emit
from app.integrations.voice import twilio_voice
from app.models import OpsUser, PhoneCall
from app.schemas.serialize import stamp

log = logging.getLogger("liner.phone")

router = APIRouter(prefix="/phone", tags=["phone"])

#: Content type for every TwiML answer. Twilio is lenient about it and a
#: proxy in between may not be.
XML = "application/xml"


async def _verified(request: Request, path: str) -> dict[str, str]:
    """The POST form, once the signature over it checks out.

    Raises `HTTPException` carrying TwiML rather than JSON -- see the module
    note. The URL hashed is the one *Twilio was configured with*, rebuilt from
    `PUBLIC_BASE_URL`, not the one this process believes it is serving: behind
    a proxy those differ and every honest request would fail, which reads
    exactly like a wrong auth token.
    """
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}

    if not settings.twilio_validate_signature:
        # Local tunnel only, and production refuses to boot this way
        # (`app/config.py`). Logged at warning so it cannot be the quiet state.
        log.warning("Twilio signature check is OFF -- %s accepted unverified", path)
        return params

    # The query string is part of what Twilio signed, and it is taken raw from
    # the request rather than rebuilt: `to=%2B1502...` and `to=+1502...` are
    # the same number and different bytes, and the signature is over bytes.
    url = twilio_voice.webhook_url(path, _base(request))
    if request.url.query:
        url = f"{url}?{request.url.query}"
    given = request.headers.get("X-Twilio-Signature", "")
    if not twilio_voice.valid_signature(url, params, given):
        log.warning(
            "Refused an unsigned call webhook on %s from %s",
            path, request.client.host if request.client else "?",
        )
        raise HTTPException(
            status_code=403,
            detail="Bad Twilio signature",
            headers={"X-Liner-Refused": "signature"},
        )
    return params


def _base(request: Request) -> str:
    """The address this install is reached at, for this request.

    `PUBLIC_BASE_URL` when set, the request's own origin otherwise. One
    function so the signature check, the stream URL and the status callback
    cannot each pick a different answer -- a signature computed against one
    host and verified against another fails every call and blames the token.
    """
    return twilio_voice.base_url(str(request.base_url))


def _spoken_error(message: str) -> Response:
    return Response(twilio_voice.say(message), media_type=XML)


@router.post("/incoming")
async def incoming_call(request: Request, db: Session = Depends(get_db)) -> Response:
    """Somebody rang the number. Answer with TwiML that opens the media socket.

    The persona is read here, at the moment the call arrives, and written onto
    the row -- so a switch thrown mid-demo takes the *next* call and never
    changes what a call in progress was. A log entry saying a call was answered
    by whichever persona happens to be selected an hour later is a log that
    cannot be read back.
    """
    # A forged request gets a bare 403 and no TwiML: answering it with a
    # spoken sentence confirms the endpoint is real and worth probing again.
    params = await _verified(request, "/api/phone/incoming")

    persona = flags.get(db, "phone_persona")
    if persona == flags.PHONE_OFF:
        return _spoken_error(
            "Thanks for calling. Nobody is available on this line right now. "
            "Please try again later."
        )

    call = PhoneCall(
        direction="in",
        call_sid=params.get("CallSid", ""),
        from_number=params.get("From", ""),
        to_number=params.get("To", ""),
        persona=persona,
        status=params.get("CallStatus", "in-progress"),
    )
    db.add(call)
    db.commit()
    db.refresh(call)

    emit(db, "phone.started", {
        "call_id": call.id, "direction": "in",
        "from": call.from_number, "persona": persona,
    })

    stream = twilio_voice.stream_url(_base(request))
    if "://" not in stream:
        # Nothing to hand Twilio: no PUBLIC_BASE_URL and a request that carried
        # no origin either. Said out loud rather than returning TwiML with an
        # empty url, which Twilio answers with an application error the caller
        # hears as a fault.
        log.error("No base URL; cannot give Twilio a stream address")
        # The row stays -- somebody really did ring, and a misconfiguration is
        # exactly when you want to see that they did -- but it says what
        # happened rather than sitting at in-progress for ever.
        call.status = "failed"
        call.ended_at = utcnow()
        db.commit()
        return _spoken_error(
            "Thanks for calling. This line is not finished being set up. "
            "Please try again later."
        )

    # The call id rides along as a custom parameter, so the socket knows which
    # row it belongs to without a second lookup by SID -- and without trusting
    # the socket to tell us who it is.
    url = f"{stream}?call={call.id}"
    return Response(
        twilio_voice.connect_stream(url, settings.phone_greeting),
        media_type=XML,
    )


@router.post("/status")
async def call_status(request: Request, db: Session = Depends(get_db)) -> Response:
    """Twilio's last word on a call. Only `completed` is subscribed to."""
    params = await _verified(request, "/api/phone/status")
    sid = params.get("CallSid", "")
    call = (
        db.query(PhoneCall).filter_by(call_sid=sid).order_by(PhoneCall.started_at.desc()).first()
        if sid else None
    )
    if call is None:
        # Not an error. A status callback for a call this instance never
        # recorded is what a reseed or a second environment looks like, and
        # answering 404 makes Twilio retry something that will never resolve.
        return Response("", media_type=XML)

    call.status = params.get("CallStatus", call.status)
    call.duration_sec = int(params.get("CallDuration") or call.duration_sec or 0)
    call.ended_at = call.ended_at or utcnow()
    db.commit()
    emit(db, "phone.ended", {
        "call_id": call.id, "status": call.status, "seconds": call.duration_sec,
    })
    return Response("", media_type=XML)


@router.post("/outbound-twiml")
async def outbound_twiml(request: Request, db: Session = Depends(get_db)) -> Response:
    """What our own phone hears when it picks up a click-to-call.

    The prospect is dialled *from here*, once somebody at Liner has actually
    answered. Ringing them first and making them listen to silence while we
    pick up is how a dealership decides we are a robocall.

    The number to dial is carried in the query string rather than looked up,
    and the signature covers the full URL including it -- so it cannot be
    rewritten into a call to somewhere expensive by anyone who finds the path.
    """
    await _verified(request, "/api/phone/outbound-twiml")
    to = request.query_params.get("to", "")
    if not to:
        return _spoken_error("No number was given for this call.")
    return Response(
        twilio_voice.dial(to, settings.twilio_number),
        media_type=XML,
    )


# --------------------------------------------------------------------------
# Ours: the controls on /ops/phone
# --------------------------------------------------------------------------

ops = APIRouter(prefix="/ops/phone", tags=["phone"])


def _call_out(row: PhoneCall) -> dict:
    return {
        "id": row.id,
        "direction": row.direction,
        "from": row.from_number,
        "to": row.to_number,
        "persona": row.persona,
        "status": row.status,
        "duration_sec": row.duration_sec,
        "conversation_id": row.conversation_id or "",
        "demo_request_id": row.demo_request_id or "",
        "started_at": stamp(row.started_at),
        "ended_at": stamp(row.ended_at),
    }


@ops.get("")
def phone_state(
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Everything the page needs: what is configured, what is missing, who
    answers, and where to point Twilio.

    The webhook URLs are *composed here* rather than typed into the page,
    because they have to match what the signature is computed against. Two
    copies of an address is how the console ends up pointing at one path and
    the check running against another, which fails every call with a message
    about signatures and no clue that a URL is the problem.
    """
    from app.agent.providers import provider_key_present

    absent = twilio_voice.missing()
    base = settings.public_base_url
    if not base:
        # Named as missing even though a request can be guessed from: these
        # URLs are pasted into somebody else's console, so "whatever host this
        # request came in on" is not an answer that can be written down.
        absent = absent + ["PUBLIC_BASE_URL"]

    calls = (
        db.query(PhoneCall).order_by(PhoneCall.started_at.desc()).limit(50).all()
    )
    persona = flags.get(db, "phone_persona")
    return {
        "configured": not absent,
        "missing": absent,
        "number": settings.twilio_number,
        "ops_number": settings.twilio_ops_number,
        "persona": persona,
        "personas": list(flags.PHONE_PERSONAS),
        # The assistant needs a model as well as a line. Named separately
        # because "the phone does not work" has two completely different
        # answers depending on which of the two is absent.
        "model_ready": settings.llm_mode == "live" and provider_key_present(),
        # Empty rather than relative when there is no base. These exist to be
        # pasted into somebody else's console, and `/api/phone/incoming` or
        # `ws:///ws/phone/media` is not an address -- showing one invites it
        # being copied, and a half-URL in Twilio fails every call.
        "webhooks": {
            "voice": twilio_voice.webhook_url("/api/phone/incoming") if base else "",
            "status": twilio_voice.webhook_url("/api/phone/status") if base else "",
            "stream": twilio_voice.stream_url() if base else "",
        },
        "greeting": settings.phone_greeting,
        "signature_checked": settings.twilio_validate_signature,
        # Which credential outbound calls authenticate with -- never the
        # credential itself. Worth reporting because it is invisible otherwise:
        # both work, and the difference only shows up on the day somebody has
        # to rotate one of them.
        "auth_source": twilio_voice.auth_source(),
        "calls": [_call_out(row) for row in calls],
    }


class PersonaBody(BaseModel):
    value: str
    reason: str = ""


@ops.post("/persona")
def set_persona(
    body: PersonaBody,
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Switch who answers. Takes effect on the next call, never on one in
    progress -- the persona is stamped on the row when the call arrives."""
    value = (body.value or "").strip().lower()
    try:
        # `by=None` deliberately: `runtime_flags.set_by_user_id` is a foreign
        # key into the *dealership's* `users`, and an ops id there is a
        # constraint violation waiting for the next write. Who threw it is on
        # the `phone.persona` event instead, which is not realm-bound.
        flags.set(db, "phone_persona", value, reason=body.reason, by=None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    emit(db, "phone.persona", {"value": value, "by": user.id})
    return phone_state(db=db, user=user)


class CallBody(BaseModel):
    to: str
    #: Optional: the demo request this call is about, so the two read together.
    demo_request_id: str = ""


@ops.post("/call")
def place_call(
    body: CallBody,
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Ring somebody. Twilio calls us first and bridges them in when we answer.

    `TWILIO_OPS_NUMBER` is the phone that rings. Refused rather than defaulted
    when it is unset: the alternative is guessing which handset to ring, and a
    wrong guess is a call to a stranger.
    """
    to = (body.to or "").strip()
    if not to:
        raise HTTPException(400, "A number to call is needed.")
    if not settings.twilio_ops_number:
        raise HTTPException(
            400,
            "TWILIO_OPS_NUMBER is not set, so there is no phone here to ring first. "
            "Set it to the handset that should pick up.",
        )

    # The prospect's number is in the URL the answer leg is signed against, so
    # it cannot be swapped by anybody who finds the path.
    answer = twilio_voice.webhook_url(
        "/api/phone/outbound-twiml?" + urlencode({"to": to})
    )
    status = twilio_voice.webhook_url("/api/phone/status")
    result = twilio_voice.place_call(settings.twilio_ops_number, answer, status)

    call = PhoneCall(
        direction="out",
        call_sid=str(result.get("sid") or ""),
        from_number=settings.twilio_number,
        to_number=to,
        # No assistant on this one. A person is ringing a person, and writing a
        # persona here would put a call nobody's AI touched into the same
        # column as the ones it did.
        persona="",
        status=str(result.get("status") or "queued"),
        placed_by_user_id=user.id,
        demo_request_id=body.demo_request_id or None,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    emit(db, "phone.started", {
        "call_id": call.id, "direction": "out", "to": to, "by": user.id,
    })
    return {"call": _call_out(call)}
