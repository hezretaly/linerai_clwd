"""Twilio SMS: the REST call that sends one message.

# PLACEHOLDER(twilio-sms): the request here has never run. No Twilio account
# in this environment and `api.twilio.com` is refused by the egress proxy. What
# is real and asserted by `make smoke` is the body it builds, which is the only
# part of a send that can be wrong in a way an account would not tell you about.

Everything about *the account* -- credentials, the signature that guards the
inbound webhook, the URLs -- lives in `integrations/twilio_account.py` and is
shared with voice. One number, one auth token, one signature recipe. What is
here is the one thing SMS has that voice does not: a message body.
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.integrations import twilio_account as account
from app.integrations.base import NotConfigured

#: A single SMS segment is 160 GSM-7 characters, or 70 if anything in it is
#: outside that alphabet -- an emoji, a curly quote a phone keyboard inserted.
#: Twilio splits a longer body and bills per segment, so this is a cost as well
#: as a courtesy. Not enforced as a hard cap: refusing to send a rep's message
#: because it ran to 170 characters would be worse than the extra segment.
SEGMENT = 160

#: What the API will not take at all. Twilio's own ceiling is 1600 characters;
#: past it the send is rejected rather than split.
MAX_BODY = 1600


def message_payload(to: str, body: str, status_url: str = "") -> dict[str, str]:
    """The form a send would POST, built separately so it can be asserted
    without being sent -- the same trick `call_payload` and `ResendSender.payload`
    use, and the only way to check this without an account."""
    form = {
        "To": to,
        "From": settings.twilio_number,
        "Body": body,
    }
    if status_url:
        form["StatusCallback"] = status_url
    return form


def segments(body: str) -> int:
    """How many messages this will really be billed as.

    Rough on purpose: the exact rule depends on which characters force UCS-2
    and on the per-segment header a concatenated message carries. It is shown
    to a rep as a hint before they send, not used for accounting.
    """
    if not body:
        return 0
    limit = SEGMENT if body.isascii() else 70
    return max(1, -(-len(body) // limit))


def send(to: str, body: str, status_url: str = "") -> dict:
    """Send one message. Returns Twilio's own JSON.

    Errors are surfaced verbatim, for the reason `place_call` surfaces them:
    Twilio's message names the actual problem -- an unverified number, a
    geographic permission, 21610 for a recipient who has opted out -- and
    summarising that into "the message failed" is how somebody spends an
    afternoon guessing.
    """
    account.check()
    if not body.strip():
        raise NotConfigured("sms", [], "There is no message to send.")
    if len(body) > MAX_BODY:
        raise NotConfigured(
            "sms", [],
            f"That message is {len(body)} characters; Twilio rejects anything over "
            f"{MAX_BODY}. Shorten it or send it in two.",
        )

    url = f"{account.API_ROOT}/Accounts/{settings.twilio_account_sid}/Messages.json"
    try:
        response = httpx.post(
            url,
            data=message_payload(to, body, status_url),
            auth=account.rest_auth(),
            timeout=account.TIMEOUT,
        )
    except httpx.HTTPError as exc:
        # Never reached Twilio at all -- blocked egress, no DNS, a timeout.
        # Distinct from a rejection, and whoever is debugging needs to know
        # which of the two it was.
        raise NotConfigured(
            "sms", [], f"Could not reach Twilio to send the message: {exc}"
        ) from None

    if response.status_code >= 400:
        raise NotConfigured(
            "sms", [], f"Twilio returned {response.status_code}: {response.text[:500]}"
        )
    return response.json()
