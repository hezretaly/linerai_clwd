"""One Twilio account, shared by the phone line and by SMS.

# PLACEHOLDER(twilio): no request built here has ever run. There is no
# `TWILIO_ACCOUNT_SID` in this environment and `api.twilio.com` is refused by
# the egress proxy besides. What is real and asserted offline by `make smoke`
# is everything either side of the wire: the signature that guards every
# public webhook, the credential the REST calls authenticate with, and
# `check()` naming the missing variable rather than failing vaguely.

**Extracted rather than copied**, which is the whole reason this module exists.
Voice and SMS are one account, one auth token, one signature recipe and one set
of webhook URLs; two copies of the signature check is how one channel starts
accepting a request the other refuses, and a security boundary is the last
place to keep two implementations of anything.

What stays with each channel is what only it has: TwiML and `place_call` in
`voice/twilio_voice.py`, the message body in `sms/twilio_sms.py`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

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
