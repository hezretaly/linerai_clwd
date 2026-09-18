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

import base64
import hashlib
import hmac
from xml.sax.saxutils import escape, quoteattr

import httpx

from app.config import settings
from app.integrations.base import NotConfigured

API_ROOT = "https://api.twilio.com/2010-04-01"
TIMEOUT = 20.0

#: What a Twilio number is worth nothing without. Named individually because
#: "Twilio is not configured" sends somebody to read three files.
REQUIRED = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_NUMBER")


def real_token() -> str:
    """The auth token, or "" when it is still the development default.

    The default exists so the signature check is testable offline at all (see
    `app/config.py`). It is not a Twilio credential, so everything that asks
    "is this account set up" has to see through it -- otherwise the dashboard
    reports a configured phone line on a fresh clone.
    """
    from app.config import DEV_TWILIO_TOKEN

    token = settings.twilio_auth_token
    return "" if token == DEV_TWILIO_TOKEN else token


def configured() -> bool:
    return not missing()


def api_key() -> tuple[str, str]:
    """The API Key pair, or ("", "") when it is not fully set.

    Both halves or neither: one on its own is not a credential, and the whole
    point of `half_key` below is that it must not quietly become one.
    """
    sid = settings.twilio_api_key_sid.strip()
    secret = settings.twilio_api_key_secret.strip()
    return (sid, secret) if sid and secret else ("", "")


def half_key() -> str:
    """The partner variable, when exactly one half of the key pair is set.

    **A silent fallback here would be the worst outcome available.** Somebody
    who sets `TWILIO_API_KEY_SID` has decided the account's master token should
    not be doing REST calls; falling back to it anyway leaves them believing
    they hold a revocable credential while every outbound call still
    authenticates with the one secret that also signs inbound webhooks. So a
    half-configured pair is reported as missing and refuses the send, rather
    than working in a way that is wrong on purpose.
    """
    sid = settings.twilio_api_key_sid.strip()
    secret = settings.twilio_api_key_secret.strip()
    if sid and not secret:
        return "TWILIO_API_KEY_SECRET"
    if secret and not sid:
        return "TWILIO_API_KEY_SID"
    return ""


def rest_auth() -> tuple[str, str]:
    """What authenticates an outbound REST call: the key pair, or the account.

    The account SID stays in the *URL* either way -- an API Key does not
    replace the account, it replaces the password. That is counterintuitive
    enough to be worth a line, since a key set without the account SID looks
    like it should work and 401s.
    """
    key = api_key()
    return key if key[0] else (settings.twilio_account_sid, settings.twilio_auth_token)


def auth_source() -> str:
    """Which of the two is in use, for the page to report. Never the secret."""
    return "api_key" if api_key()[0] else "auth_token"


def missing() -> list[str]:
    """Exactly which of them are absent, in the order they appear in `.env`."""
    have = {
        "TWILIO_ACCOUNT_SID": settings.twilio_account_sid,
        "TWILIO_AUTH_TOKEN": real_token(),
        "TWILIO_NUMBER": settings.twilio_number,
    }
    absent = [key for key in REQUIRED if not have[key]]
    # Appended rather than in REQUIRED: the key is optional, so it is only ever
    # "missing" when its other half is present and somebody is halfway through
    # setting it up.
    partner = half_key()
    if partner:
        absent.append(partner)
    return absent


def check() -> None:
    """Raise the typed error the UI renders, naming what is absent."""
    absent = missing()
    if not absent:
        return
    partner = half_key()
    if absent == [partner]:
        # Everything else is set, so this is not "the phone is off" -- it is a
        # key somebody is halfway through swapping in, and saying the line is
        # off would send them to check the wrong three variables.
        raise NotConfigured(
            "phone",
            absent,
            f"Half of a Twilio API Key is set, so {partner} is missing. Both "
            "halves or neither: with one, outbound calls would silently fall "
            "back to the account's auth token -- the same secret that signs "
            "inbound webhooks, which is what a key exists to stop.",
        )
    raise NotConfigured(
        "phone",
        absent,
        "The phone line is off. Set "
        + ", ".join(absent)
        + " to answer a real number. Left empty on purpose: a Twilio "
        "account present for something else should not start this system "
        "picking up calls it has not been told to take.",
    )


# --------------------------------------------------------------------------
# The signature on an inbound webhook
# --------------------------------------------------------------------------

def signature_for(url: str, params: dict[str, str], token: str = "") -> str:
    """Twilio's own recipe, reimplemented rather than depended on.

    The full URL, then every POST parameter appended as `key + value` in
    **sorted key order**, HMAC-SHA1 under the auth token, base64. Sorting is
    the part that is easy to get wrong and impossible to notice: a form arrives
    in whatever order the sender wrote it, and hashing it in arrival order
    validates roughly one request in n factorial.

    `token` is an argument so a rotation can be verified against the old value
    and so the check is testable without putting a secret in the environment.
    """
    payload = url + "".join(
        f"{key}{params[key]}" for key in sorted(params)
    )
    digest = hmac.new(
        (token or settings.twilio_auth_token).encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def valid_signature(url: str, params: dict[str, str], given: str, token: str = "") -> bool:
    """`compare_digest`, never `==`.

    A string comparison that returns early leaks where it stopped matching, and
    this one is reachable by anybody who finds the URL -- which is the whole
    point of a webhook. It is also why an unset token refuses rather than
    computing a signature over an empty key: with no token every request is
    forgeable and "not configured" must not read as "allowed".
    """
    secret = token or settings.twilio_auth_token
    if not secret or not given:
        return False
    return hmac.compare_digest(signature_for(url, params, secret), given)


def base_url(request_base: str = "") -> str:
    """Where this install is reached from, for the URL a signature covers.

    `PUBLIC_BASE_URL` when it is set, and the address the request arrived on
    otherwise -- the same order `api/redirect.py` uses to build a tracked link.
    Guessing from the request is right behind the shipped nginx config and
    wrong behind a proxy that drops `Host`, which is exactly what the setting
    exists to settle.
    """
    return (settings.public_base_url or request_base or "").rstrip("/")


def webhook_url(path: str, base: str = "") -> str:
    """The absolute URL Twilio was told to call, which is what it signed.

    The signature covers the address, so this must be the URL Twilio has in its
    console -- not the one this process thinks it is serving. Behind a proxy
    those differ (`http://127.0.0.1:8000/...` against
    `https://liner.example.com/...`), and the mismatch fails every request with
    a valid signature, which reads exactly like a wrong auth token.
    `PUBLIC_BASE_URL` is what settles it.
    """
    root = (base or settings.public_base_url or "").rstrip("/")
    return f"{root}/{path.lstrip('/')}"


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
