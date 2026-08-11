"""Mail coming back in, from the Cloudflare Worker.

The only endpoint in this app that no session guards, so the HMAC is the whole
door: without it anyone who finds the URL can write into a buyer's history.

Every delivery leaves an ``inbound_emails`` receipt whatever happens to it,
including the ones refused. A 401 into the void is unfalsifiable -- the
operator sees no reply arriving and has no way to tell a broken signature from
a broken Cloudflare route from a buyer who never wrote back. The receipts are
what the setup page reads.

Nothing here wakes the agent. A reply lands as an activity a rep reads; Liner
answering email on its own needs guards, a rate limit and a loop-breaker for
auto-responders, and none of that exists.
"""

from __future__ import annotations

import hmac
import re
from hashlib import sha256

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import matching
from app.config import settings
from app.api.deps import current_user
from app.db import get_db, utcnow
from app.events import emit
from app.schemas.serialize import iso
from app.models import (
    Appointment,
    Conversation,
    Escalation,
    InboundEmail,
    Lead,
    Outreach,
    User,
)

router = APIRouter(tags=["email"])

# reply+<token>@domain. The local part is what carries the thread; the domain
# is whatever Cloudflare routed, and matching on it would break the moment a
# dealer forwards from a second address.
REPLY_RE = re.compile(r"reply\+([A-Za-z0-9_-]{6,64})@", re.IGNORECASE)


class InboundBody(BaseModel):
    model_config = {"extra": "allow"}

    messageId: str = ""
    from_: str = ""
    to: str = ""
    subject: str = ""
    text: str = ""
    html: str = ""
    inReplyTo: str = ""
    receivedAt: str = ""


def signature_for(raw: bytes) -> str:
    return hmac.new(settings.webhook_secret.encode(), raw, sha256).hexdigest()


def authenticate(raw: bytes, signature: str, shared: str) -> str:
    """Which credential proved this delivery, or "" if none did.

    Two are accepted because two are in use. `X-Liner-Signature` is an HMAC
    over the exact bytes, so it authenticates the *body* as well as the
    sender -- a truncated or edited payload fails. `X-Webhook-Secret` is a
    plain shared secret, which authenticates only the sender; it is what the
    deployed Cloudflare Worker sends, and over TLS to a known origin that is a
    normal webhook arrangement rather than a hole.

    Both are compared in constant time. Preferring the signature when both are
    present means moving the Worker to HMAC is a Worker-only change.
    """
    if signature and hmac.compare_digest(signature_for(raw), signature):
        return "signature"
    if shared and hmac.compare_digest(settings.webhook_secret, shared):
        return "shared_secret"
    return ""


def _receipt(db: Session, **kwargs) -> InboundEmail:
    row = InboundEmail(**kwargs)
    db.add(row)
    db.commit()
    return row


# Two paths, one handler. `/api/emails/inbound` is what the deployed Worker
# posts to; `/api/inbound-email` is what this app documented first. Changing
# either would mean a redeploy on one side to fix a rename on the other, and
# an alias costs a line.
@router.post("/inbound-email")
@router.post("/emails/inbound")
async def receive(
    request: Request,
    x_liner_signature: str = Header(default=""),
    x_webhook_secret: str = Header(default=""),
    db: Session = Depends(get_db),
) -> dict:
    raw = await request.body()

    # No secret configured means no door at all, so the endpoint is shut rather
    # than open. An unauthenticated mail intake is worse than a missing one.
    if not settings.webhook_secret:
        raise HTTPException(
            503,
            "WEBHOOK_SECRET is not set, so inbound mail cannot be authenticated "
            "and is refused. See integrations/email/worker/README.md.",
        )

    proved_by = authenticate(raw, x_liner_signature or "", x_webhook_secret or "")
    if not proved_by:
        # Recorded before refusing: a wrong shared secret is the single most
        # likely reason mail stops arriving, and it is invisible otherwise.
        # Naming which headers arrived turns "nothing works" into "the Worker
        # is sending the header I am not reading".
        offered = [
            name for name, value in (
                ("X-Liner-Signature", x_liner_signature),
                ("X-Webhook-Secret", x_webhook_secret),
            ) if value
        ]
        _receipt(
            db, outcome="bad_signature",
            detail=(
                f"Sent {' and '.join(offered)}, and neither matched WEBHOOK_SECRET."
                if offered else
                "No X-Liner-Signature or X-Webhook-Secret header was sent at all."
            ),
        )
        raise HTTPException(401, "Bad signature")

    try:
        payload = InboundBody.model_validate_json(raw)
    except Exception as exc:
        _receipt(db, outcome="malformed", detail=str(exc)[:500])
        raise HTTPException(400, "Malformed payload") from None

    data = payload.model_dump()
    message_id = (data.get("messageId") or "").strip()
    # The Worker sends `from`, which is a Python keyword; pydantic keeps it in
    # the extras rather than on the field named from_.
    sender = (data.get("from") or data.get("from_") or "").strip()
    to = (data.get("to") or "").strip()
    subject = (data.get("subject") or "").strip()
    body = (data.get("text") or data.get("html") or "").strip()
    in_reply_to = (data.get("inReplyTo") or "").strip()

    envelope = {
        "message_id": message_id, "from_address": sender, "to_address": to,
        "subject": subject, "body": body, "in_reply_to": in_reply_to,
    }

    # Idempotent on the provider's id. Cloudflare retries, and a retry must not
    # give a buyer two replies on their timeline.
    if message_id:
        seen = (
            db.query(InboundEmail)
            .filter(InboundEmail.message_id == message_id,
                    InboundEmail.outcome == "accepted")
            .first()
        )
        if seen is not None:
            _receipt(db, outcome="duplicate", detail=f"Already accepted as {seen.id}.",
                     **envelope)
            return {"ok": True, "outcome": "duplicate"}

    lead, outreach, matched_by = _resolve(db, to, in_reply_to, sender)
    if lead is None:
        _receipt(
            db, outcome="unresolved", **envelope,
            detail=(
                "No reply token, no matching message id, and the From address is not "
                "on any lead. Kept rather than dropped -- someone really wrote in."
            ),
        )
        return {"ok": True, "outcome": "unresolved"}

    record = Outreach(
        lead_id=lead.id,
        appointment_id=outreach.appointment_id if outreach else None,
        channel="email",
        direction="in",
        kind="reply",
        to_address=sender,
        subject=subject,
        body=body,
        provider="inbound",
        provider_message_id=message_id or None,
        in_reply_to=in_reply_to or None,
        status="sent",
        sent_at=utcnow(),
    )
    db.add(record)
    db.commit()

    _receipt(db, outcome="accepted", matched_by=matched_by, lead_id=lead.id,
             outreach_id=record.id, detail=f"Authenticated by {proved_by}.", **envelope)

    reopened = _reopen(db, outreach, lead)
    emit(db, "email.received", {
        "lead_id": lead.id, "outreach_id": record.id,
        "matched_by": matched_by, "reopened": reopened,
    })
    return {"ok": True, "outcome": "accepted", "lead_id": lead.id}


def _resolve(
    db: Session, to: str, in_reply_to: str, sender: str
) -> tuple[Lead | None, Outreach | None, str]:
    """Who wrote in, by the narrowest rule that fits.

    Order matters. The token is exact and was minted by us; the message id is
    exact but only present when the client threaded properly; the From address
    is the loosest and comes last, because two people can share one.
    """
    found = REPLY_RE.search(to or "")
    if found:
        token = found.group(1)
        sent = db.query(Outreach).filter_by(reply_token=token).first()
        if sent is None:
            # New tokens are lowercase so this cannot bite, but a mail server
            # is entitled to rewrite the local part's case and an older token
            # is mixed. Falling through to the From address would still
            # "work", quietly, on the loosest rule available -- which is how a
            # reply ends up on the wrong buyer.
            sent = (
                db.query(Outreach)
                .filter(func.lower(Outreach.reply_token) == token.lower())
                .first()
            )
        if sent is not None and sent.lead_id:
            lead = db.query(Lead).filter_by(id=sent.lead_id).one_or_none()
            if lead is not None:
                return lead, sent, "reply_token"

    if in_reply_to:
        sent = (
            db.query(Outreach)
            .filter(Outreach.provider_message_id == in_reply_to)
            .first()
        )
        if sent is not None and sent.lead_id:
            lead = db.query(Lead).filter_by(id=sent.lead_id).one_or_none()
            if lead is not None:
                return lead, sent, "in_reply_to"

    # The same matcher the importer, manual entry and book_appointment use. A
    # name is never part of it, so a stranger stays a stranger.
    lead = matching.match_lead(db, sender, "")
    if lead is not None:
        return lead, None, "from_address"

    return None, None, ""


def _reopen(db: Session, outreach: Outreach | None, lead: Lead) -> bool:
    """A buyer answering the question a rep asked is the rep's turn again.

    Only reopens an escalation that was already claimed and closed off -- a
    thread nobody had flagged is not made urgent by a reply, and turning every
    inbound message into a queue entry is how the queue stops meaning anything.
    """
    convo_ids = [
        c.id for c in db.query(Conversation).filter_by(lead_id=lead.id).all()
    ]
    if outreach is not None and outreach.appointment_id:
        appt = db.query(Appointment).filter_by(id=outreach.appointment_id).one_or_none()
        if appt is not None and appt.conversation_id:
            convo_ids = [appt.conversation_id]
    if not convo_ids:
        return False

    claimed = (
        db.query(Escalation)
        .filter(
            Escalation.conversation_id.in_(convo_ids),
            Escalation.claimed_at.is_not(None),
        )
        .order_by(Escalation.created_at.desc())
        .first()
    )
    if claimed is None:
        return False

    claimed.claimed_at = None
    claimed.claimed_by_user_id = None
    db.commit()
    emit(db, "handoff.triggered", {
        "conversation_id": claimed.conversation_id, "action": "buyer_replied",
    })
    return True


# --------------------------------------------------------------------------
# What the setup page reads. Dealer session required -- unlike the intake
# above, which is guarded by the HMAC because Cloudflare has no session.
# --------------------------------------------------------------------------


@router.get("/email/receipts")
def receipts(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """The last deliveries and what happened to each.

    Refusals included, and they are the point: a rep watching replies not
    arrive needs to tell a wrong shared secret from a Cloudflare route that
    was never created from a buyer who simply has not written back.
    """
    rows = (
        db.query(InboundEmail)
        .order_by(InboundEmail.created_at.desc())
        .limit(40)
        .all()
    )
    return {
        "receipts": [
            {
                "id": r.id,
                "outcome": r.outcome,
                "message_id": r.message_id,
                "from_address": r.from_address,
                "to_address": r.to_address,
                "subject": r.subject,
                "matched_by": r.matched_by,
                "lead_id": r.lead_id,
                "detail": r.detail,
                "created_at": iso(r.created_at),
            }
            for r in rows
        ],
        # The address family a reply has to arrive on. Empty domain means the
        # Reply-To is omitted entirely rather than pointing somewhere that
        # would bounce, and the page says so.
        "reply_domain": settings.sending_domain,
        "endpoint": "/api/inbound-email",
        "signature_header": "X-Liner-Signature",
    }


@router.get("/email/messages")
def messages(
    box: str = "all",
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Every email this dealership has sent or received, newest first.

    A union rather than one table, for the same reason the conversations list
    is one: a reply nobody could place has no `outreach` row -- there is no
    buyer to hang it on -- and it exists only as a receipt. Listing just
    `outreach` would mean a stranger writing to sales@ is visible on the
    diagnostics strip and nowhere a manager would ever look.

    Drafts are deliberately absent. Nothing here stores one: a draft is
    composed from the lead's state when the composer opens
    (`GET /api/leads/{id}/outreach?draft=1`) and exists only in the rep's
    browser until they press send. An empty "Drafts" tab would claim a feature
    that is not there.
    """
    rows = (
        db.query(Outreach)
        .filter(Outreach.channel == "email")
        .order_by(Outreach.created_at.desc())
        .limit(500)
        .all()
    )
    lead_ids = {r.lead_id for r in rows if r.lead_id}
    leads = {
        lead.id: lead
        for lead in (
            db.query(Lead).filter(Lead.id.in_(lead_ids)).all() if lead_ids else []
        )
    }

    out = []
    for r in rows:
        lead = leads.get(r.lead_id or "")
        out.append({
            "id": r.id,
            "kind": "message",
            "direction": r.direction,
            "address": r.to_address,
            "subject": r.subject,
            "body": r.body,
            "status": r.status,
            "error": r.error,
            "provider": r.provider,
            "delivered_externally": r.provider not in {"", "outbox", "console"},
            "lead_id": r.lead_id,
            "lead_name": (lead.name if lead else "") or "",
            "at": iso(r.sent_at or r.created_at),
        })

    # Mail that arrived and could not be placed. It has no lead by definition,
    # so nothing on any buyer page will ever show it.
    for r in (
        db.query(InboundEmail)
        .filter(InboundEmail.outcome == "unresolved")
        .order_by(InboundEmail.created_at.desc())
        .limit(200)
        .all()
    ):
        out.append({
            "id": r.id,
            "kind": "unmatched",
            "direction": "in",
            "address": r.from_address,
            "subject": r.subject,
            "body": r.body,
            "status": "unmatched",
            "error": "",
            "provider": "inbound",
            "delivered_externally": True,
            "lead_id": None,
            "lead_name": "",
            "at": iso(r.created_at),
        })

    out.sort(key=lambda m: m["at"] or "", reverse=True)

    counts = {
        key: sum(1 for m in out if _in_box(m, key))
        for key in ("all", "received", "sent", "failed", "unmatched")
    }
    shown = [m for m in out if _in_box(m, box)]
    if q:
        needle = q.lower()
        shown = [
            m for m in shown
            if needle in f"{m['address']} {m['subject']} {m['body']} {m['lead_name']}".lower()
        ]
    return {"messages": shown[:200], "counts": counts}


def _in_box(m: dict, box: str) -> bool:
    """One definition of each box, used for the counts and for the filtering.
    Two copies is how a tab ends up saying 12 and showing 9."""
    if box == "received":
        return m["direction"] == "in" and m["kind"] == "message"
    if box == "sent":
        return m["direction"] == "out" and m["status"] == "sent"
    # Refusals live here: an allow-list block is a send that did not happen,
    # and it is the one a manager has to notice.
    if box == "failed":
        return m["direction"] == "out" and m["status"] != "sent"
    if box == "unmatched":
        return m["kind"] == "unmatched"
    return True


@router.get("/email/replyable")
def replyable(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Sends a reply could arrive against, for the setup page's test.

    Choosing the target explicitly is deliberate: a test reply lands on a real
    buyer's timeline, and a rep should never have to guess whose.
    """
    rows = (
        db.query(Outreach)
        .filter(Outreach.direction == "out", Outreach.reply_token.is_not(None))
        .order_by(Outreach.created_at.desc())
        .limit(25)
        .all()
    )
    out = []
    for row in rows:
        lead = db.query(Lead).filter_by(id=row.lead_id).one_or_none() if row.lead_id else None
        out.append({
            "id": row.id,
            "reply_token": row.reply_token,
            "subject": row.subject,
            "to_address": row.to_address,
            "lead_id": row.lead_id,
            "lead_name": (lead.name if lead else None) or "Unknown",
            "created_at": iso(row.created_at),
        })
    return {"sends": out}


class TestSend(BaseModel):
    to: str


@router.post("/email/test-send")
def test_send(
    body: TestSend,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """One real send down the real path, recorded like any other.

    Not a special case that skips the guards: the same allow-list refuses it,
    the same reply token is minted, the same row is written. A test that took a
    shortcut would prove the shortcut works.
    """
    from app import outreach_send
    from app.integrations.registry import get_email_sender

    sender = get_email_sender()
    token = outreach_send.mint_reply_token(db)
    record = Outreach(
        lead_id=None, sent_by_user_id=user.id, channel="email", kind="test",
        to_address=body.to, subject="Liner test message",
        body="This is a test from the Liner dashboard. Replying to it proves the "
             "round trip works: the reply address on this message routes back "
             "into the system.",
        provider=sender.name, status="queued", reply_token=token,
    )
    db.add(record)
    db.commit()

    blocked = outreach_send.blocked_reason(sender, body.to)
    if blocked:
        record.status = "failed"
        record.error = blocked
        db.commit()
        return {"status": "failed", "error": blocked, "provider": sender.name}

    try:
        result = sender.send(
            body.to, record.subject, record.body,
            reply_to=outreach_send.reply_to_address(token),
        )
    except Exception as exc:  # NotConfigured, or anything the provider raised
        record.status = "failed"
        record.error = str(exc)
        db.commit()
        return {"status": "failed", "error": str(exc), "provider": sender.name}

    record.provider_message_id = result.message_id
    record.status = result.status
    record.error = result.detail if result.status != "sent" else ""
    record.sent_at = utcnow()
    db.commit()
    return {
        "status": result.status,
        "error": result.detail,
        "provider": result.provider,
        # Without a domain there is no Reply-To at all, which is worth saying:
        # the mail may go out and still be unreplyable.
        "reply_to": outreach_send.reply_to_address(token),
    }


class TestInbound(BaseModel):
    outreach_id: str


@router.post("/email/test-inbound")
def test_inbound(
    body: TestInbound,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Post a signed sample through the live handler.

    Deliberately over HTTP to our own endpoint rather than calling `receive()`
    in-process: the signature check and the header plumbing are exactly the
    parts that break, and a test that skipped them would pass while real
    deliveries 401.
    """
    import json

    import httpx

    sent = db.query(Outreach).filter_by(id=body.outreach_id).one_or_none()
    if sent is None or not sent.reply_token:
        raise HTTPException(404, "No such send, or it carries no reply token.")

    domain = settings.sending_domain or "example.invalid"
    payload = {
        "messageId": f"<test-{sent.reply_token}-{utcnow().isoformat()}>",
        "from": sent.to_address,
        "to": f"reply+{sent.reply_token}@{domain}",
        "subject": f"Re: {sent.subject}",
        "text": "This is a test reply posted from the Liner dashboard.",
        "inReplyTo": sent.provider_message_id or "",
        "receivedAt": utcnow().isoformat(),
    }
    raw = json.dumps(payload).encode()
    try:
        response = httpx.post(
            "http://127.0.0.1:8000/api/inbound-email",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Liner-Signature": signature_for(raw),
            },
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach the inbound endpoint: {exc}") from None

    return {"status": response.status_code, "body": response.json() if response.content else {}}
