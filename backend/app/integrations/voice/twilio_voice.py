"""Twilio: a real phone number, in both directions.

# PLACEHOLDER(twilio): no request in this file has ever run. There is no
# `TWILIO_ACCOUNT_SID` in this environment and `api.twilio.com` is refused by
# the egress proxy besides. What *is* real and asserted offline by `make smoke`
# is everything either side of the wire: the signature check that guards the
# public webhook, the TwiML each endpoint returns, the body an outbound call
# would post, and `check()` naming the missing variable rather than failing
# vaguely. Only the request itself is unproven.

Two directions, and they are not the same feature:

**Inbound.** Somebody rings the number. Twilio POSTs a form to
`/api/phone/incoming`; we answer with TwiML telling it to open a Media Streams
WebSocket back to `/ws/phone/media`, and `app/phone_bridge.py` sits between
that socket and OpenAI Realtime. The assistant is on the line.

**Outbound.** Somebody at Liner presses Call on `/ops/phone`. Twilio rings
*our* phone first and bridges the prospect in once we pick up, which is the
right way round: the alternative rings the prospect and makes them wait while
we answer, and a dealership whose phone was rung by silence does not call back.

**The signature check is the whole security model for inbound.** That webhook
is a public URL that will be found -- Twilio's own IP ranges are published and
nothing else about the endpoint is secret. Without it anyone who guesses the
path can make this system open a Realtime session, which costs money by the
minute, and can hand it a `From` number that later reaches a rep as a lead.
`X-Twilio-Signature` is an HMAC-SHA1 over the full URL with the POST parameters
appended in key order, keyed on the auth token -- so it covers the body and the
address it was sent to, and a signature captured from one endpoint cannot be
replayed against another.

There is deliberately no fake provider. With nothing configured the page says
which variables are missing, for the same reason `UnconfiguredVoiceProvider`
does: a scripted call would prove nothing about the only things a telephony
vendor decides, which are audio quality, latency and whether the call connects.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config import settings
from app.integrations import twilio_account as account
from app.integrations.base import NotConfigured

API_ROOT = account.API_ROOT
TIMEOUT = account.TIMEOUT

# Re-exported so the two channels read the same names. Importing the module and
# reaching through it would work too; naming them here means a caller cannot
# accidentally bind a *copy* of one at import time.
check = account.check
configured = account.configured
missing = account.missing
real_token = account.real_token
rest_auth = account.rest_auth
auth_source = account.auth_source
api_key = account.api_key
half_key = account.half_key
signature_for = account.signature_for
valid_signature = account.valid_signature
base_url = account.base_url
webhook_url = account.webhook_url


def stream_url(base: str = "") -> str:
    """`wss://` for the Media Streams socket, derived from the same base.

    Derived rather than configured separately: two settings for one address is
    how the webhook points at production and the stream at a laptop.

    **Under `/ws/`, not `/api/`, and that is a deployment fact rather than
    taste.** The shipped nginx config carries the `Upgrade` and `Connection`
    headers on `location /ws/` and sets `Connection ""` on everything else, so
    a socket served under `/api/` connects, plays the greeting and then goes
    silent on any install using it -- with nothing in the app's log, because
    the app never saw the upgrade. `/ws/dealer` already settled this.
    """
    root = (base or settings.public_base_url or "").rstrip("/")
    scheme = "wss" if root.startswith("https://") else "ws"
    host = root.split("://", 1)[-1]
    return f"{scheme}://{host}/ws/phone/media"


# --------------------------------------------------------------------------
# TwiML
# --------------------------------------------------------------------------

def connect_stream(ws_url: str, greeting: str = "") -> str:
    """Hand the call to the media socket.

    `<Connect><Stream>` rather than `<Start><Stream>`: Start forks a copy of
    the audio and lets the call carry on to the next verb, which is for
    transcription. Connect *is* the call -- bidirectional, and the assistant is
    the other end of it.

    An optional `<Say>` first, for the same reason `/call` plays a pre-roll
    rather than asking the model to open: it is the same words every time, it
    cannot be improvised into the caller's line, and it costs no generated
    audio.
    """
    opening = f"<Say>{escape(greeting)}</Say>" if greeting.strip() else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response>{opening}"
        f"<Connect><Stream url={quoteattr(ws_url)} /></Connect>"
        "</Response>"
    )


def dial(number: str, caller_id: str = "", timeout: int = 30) -> str:
    """Bridge this leg to a person. The outbound half.

    `callerId` is the Twilio number rather than the prospect's own: a carrier
    will not let you present a number you do not own, and one that does is
    spoofing.
    """
    attrs = f" timeout={quoteattr(str(int(timeout)))}"
    if caller_id:
        attrs += f" callerId={quoteattr(caller_id)}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Dial{attrs}>{escape(number)}</Dial></Response>"
    )


def say(message: str) -> str:
    """One sentence and hang up. What a caller hears when the line is up but
    the assistant cannot run -- no model key, the switch off, a bridge that
    would not open. Better than a dead line, which reads as a wrong number."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{escape(message)}</Say><Hangup /></Response>"
    )


# --------------------------------------------------------------------------
# Placing a call
# --------------------------------------------------------------------------

def call_payload(to: str, answer_url: str, status_url: str = "") -> dict[str, str]:
    """The form an outbound call would POST, built separately so it can be
    asserted without being sent -- the same trick `ResendSender.payload` uses,
    and the only way to check this without an account."""
    body = {
        "To": to,
        "From": settings.twilio_number,
        "Url": answer_url,
        "Method": "POST",
    }
    if status_url:
        body["StatusCallback"] = status_url
        body["StatusCallbackMethod"] = "POST"
        # Only the ends. Twilio will post `queued`, `initiated` and `ringing`
        # as well, and three extra writes per call buy a log nobody reads.
        body["StatusCallbackEvent"] = "completed"
    return body


def place_call(to: str, answer_url: str, status_url: str = "") -> dict:
    """Ring `to`, and play it whatever `answer_url` returns when it answers.

    Returns Twilio's own JSON. Errors are surfaced verbatim for the reason
    `OpenAIRealtimeProvider.mint_session` surfaces OpenAI's: the vendor's
    message names the actual problem -- an unverified number, a geographic
    permission, a bad SID -- and summarising it into "the call failed" is how
    somebody spends an afternoon guessing.
    """
    check()
    url = f"{API_ROOT}/Accounts/{settings.twilio_account_sid}/Calls.json"
    try:
        response = httpx.post(
            url,
            data=call_payload(to, answer_url, status_url),
            auth=rest_auth(),
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        # Never reached Twilio at all -- blocked egress, no DNS, a timeout.
        # Distinct from a rejection, and whoever is debugging needs to know
        # which of the two it was.
        raise NotConfigured(
            "phone", [], f"Could not reach Twilio to place the call: {exc}"
        ) from None

    if response.status_code >= 400:
        raise NotConfigured(
            "phone", [], f"Twilio returned {response.status_code}: {response.text[:500]}"
        )
    return response.json()
