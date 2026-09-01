"""The dealership's mailbox: what a manager reads, and what they send.

Split from `inbound_email.py` when composing arrived. That module is the door
Cloudflare posts through -- no session, an HMAC for a lock, and a background
pass that files what came in. This one is ordinary dashboard surface behind an
ordinary session. Keeping them in one file meant the app's least-guarded
endpoint sat in the same place as its most ordinary ones, which is the sort of
neighbourhood where a `Depends(current_user)` goes missing unnoticed.

Every URL here is unchanged by the move.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import matching, outreach_send
from app.api.deps import current_user
from app.api.inbound_email import signature_for
from app.config import settings
from app.db import get_db, utcnow
from app import email_agent, flags
from app.email_intake import is_ours
from app.email_threads import EXCHANGE_THRESHOLD
from app.email_threads import threads as email_threads_for
from app.events import emit
from app.integrations.registry import get_email_sender
from app.schemas.serialize import iso, outreach_out, stamp
from app.models import EmailReplyDue, InboundEmail, Lead, Outreach, User

router = APIRouter(tags=["email"])

#: How many messages one request returns. The mailbox loads more on demand
#: rather than everything at once -- a dealership a year in has thousands.
PAGE = 100

#: How far back a single request will look at all. Deliberately generous: the
#: counts are computed from this set, so a ceiling that bites makes a tab
#: undercount rather than a page truncate. If a mailbox ever reaches it, the
#: fix is counting in SQL, not raising the number again.
CEILING = 5000


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
                "created_at": stamp(r.created_at),
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
    limit: int = PAGE,
    offset: int = 0,
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
        .limit(CEILING)
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
            "at": stamp(r.sent_at or r.created_at),
        })

    # Mail that arrived and could not be placed. It has no lead by definition,
    # so nothing on any buyer page will ever show it -- which is the whole
    # reason this list is a union.
    #
    # **Except what was addressed to us.** `support@`, `founder@` and `cto@`
    # are Liner's own boxes and their unplaced mail belongs in `/ops`, which
    # already lists it. Without this a stranger writing to our support desk was
    # readable by every rep at every dealership: the same realm leak
    # `_lead_from` is given a rule for, arriving through the list instead.
    for r in (
        db.query(InboundEmail)
        .filter(InboundEmail.outcome == "unresolved")
        .order_by(InboundEmail.created_at.desc())
        .limit(CEILING)
        .all()
    ):
        if is_ours(r.to_address):
            continue
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
            "at": stamp(r.created_at),
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

    # A page, and the honest size of what it came from. Returning a slice while
    # the tab counted every row is how a box said 230 and listed 200 -- the
    # same "says 12, shows 9" bug `_in_box` exists to prevent, arriving through
    # the back door of a silent cap. The count stays the true total, because a
    # manager asking how much mail there is wants the answer; the list says how
    # far down it goes.
    start = max(offset, 0)
    end = start + max(min(limit, CEILING), 1)
    return {
        "messages": shown[start:end],
        "counts": counts,
        "matching": len(shown),
        "offset": start,
        "has_more": end < len(shown),
    }


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


class FlagBody(BaseModel):
    value: str
    reason: str = ""


@router.get("/email/agent")
def agent_state(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Whether Liner is answering email, and every brake behind that answer.

    Both switches are reported rather than one boolean, because they fail
    differently and a rep looking at an "off" needs to know which one to reach
    for: `EMAIL_AGENT` in `.env` is the deployment saying this dealership has
    not turned it on and needs a restart; the runtime flag is the one somebody
    threw, and can be thrown back here.
    """
    verdict = email_agent.enabled(db)
    # The last few messages Liner declined to answer, and why. This is the
    # question a person actually has -- "it did not reply, is that on purpose?"
    # -- and the reason was only on the receipt, which is a diagnostics strip
    # nobody opens until they already suspect something.
    declined = [
        {
            "id": r.id,
            "from_address": r.from_address,
            "subject": r.subject,
            "detail": r.detail.split("Liner did not reply: ", 1)[-1],
            "at": stamp(r.created_at),
        }
        for r in (
            db.query(InboundEmail)
            .filter(InboundEmail.detail.contains("Liner did not reply"))
            .order_by(InboundEmail.created_at.desc())
            .limit(5)
            .all()
        )
    ]
    return {
        "on": verdict.allowed,
        "reason": verdict.reason,
        "detail": verdict.detail,
        "declined": declined,
        # Named separately so the page can say *which* is off. One boolean
        # would send somebody editing `.env` to undo a dashboard switch.
        "allowed_by_env": settings.email_agent,
        "flag": flags.get(db, "email_agent"),
        "flags": [
            {**row, "updated_at": stamp(row["updated_at"])}
            for row in flags.all_flags(db)
        ],
        "cooldown_minutes": settings.email_reply_cooldown_minutes,
        "hourly_ceiling": settings.email_replies_per_hour,
        # A model has to exist to write with, and that is a third thing that
        # can be off. Reported separately because it is fixed in a different
        # place from either switch.
        "live_model": settings.llm_mode == "live",
        # What is queued and when it fires. Every reply waits, so "nothing has
        # happened yet" is the normal state for a few minutes -- and without
        # this the wait is indistinguishable from the agent being off.
        "waiting": [
            {
                "id": r.id,
                "lead_id": r.lead_id,
                "due_at": stamp(r.due_at),
                "created_at": stamp(r.created_at),
            }
            for r in (
                db.query(EmailReplyDue)
                .filter(EmailReplyDue.state == "waiting")
                .order_by(EmailReplyDue.due_at.asc())
                .limit(20)
                .all()
            )
        ],
        "recent": [
            {
                "id": r.id, "lead_id": r.lead_id, "state": r.state,
                "detail": r.detail, "at": stamp(r.resolved_at or r.created_at),
            }
            for r in (
                db.query(EmailReplyDue)
                .filter(EmailReplyDue.state != "waiting")
                .order_by(EmailReplyDue.created_at.desc())
                .limit(10)
                .all()
            )
        ],
    }


@router.post("/email/agent")
def set_agent(
    body: FlagBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Throw the switch. Takes effect on the next delivery, not the next deploy.

    Open to any rep, deliberately. This is the control somebody reaches for
    while the inbox is being hammered, and gating it behind a manager means the
    person watching it happen cannot stop it.
    """
    value = (body.value or "").strip().lower()
    if value not in ("on", "off"):
        raise HTTPException(400, "value must be 'on' or 'off'")
    flags.set(db, "email_agent", value, reason=body.reason, by=user.id)
    emit(db, "email.agent", {"value": value, "by": user.id})
    return agent_state(db=db, user=user)


@router.get("/email/threads")
def email_threads(
    box: str = "open",
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Everyone the dealership is exchanging mail with, one row each.

    The other half of this page. The list below it is messages, which is what
    you want when hunting a particular send; this is people, which is what you
    want when deciding who to answer next -- and four messages with one buyer
    are one relationship, not four things to read.

    `box` slices the same rows the counts are computed from, so a tab cannot
    say 12 and show 9. **Open** is the default because it is the working list:
    everything that has not yet become a conversation, which is what this view
    is for. `graduated` is the rest -- those buyers are in
    `/app/conversations` too, and this is where you see why.
    """
    rows = email_threads_for(db)
    counts = {key: sum(1 for r in rows if _in_thread_box(r, key))
              for key in ("all", "open", "graduated", "waiting", "strangers")}
    return {
        "threads": [
            {**r, "at": stamp(r["at"]), "last_body": (r["last_body"] or "")[:280]}
            for r in rows if _in_thread_box(r, box)
        ],
        "counts": counts,
        "threshold": EXCHANGE_THRESHOLD,
    }


def _in_thread_box(row: dict, box: str) -> bool:
    """One definition of each tab, for the counts and the filter both."""
    if box == "open":
        return not row["graduated"]
    if box == "graduated":
        return row["graduated"]
    if box == "waiting":
        return row["waiting"]
    if box == "strangers":
        return row["kind"] == "stranger"
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
        .filter(
            Outreach.direction == "out",
            Outreach.reply_token.is_not(None),
            # A send with no lead has nowhere for a reply to land -- the test
            # send from this very page is one -- and the dropdown promises
            # which buyer it will appear on. Offering one would make the page
            # lie and file the result as unresolved.
            Outreach.lead_id.is_not(None),
        )
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
            "created_at": stamp(row.created_at),
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
            from_address=outreach_send.dealership_from(db, sender),
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


# --------------------------------------------------------------------------
# Writing one. Everything above reads; this is the only thing on this page
# that puts mail on the wire, and it goes down the same path a lead-level
# follow-up does -- same guard, same reply token, same row.
# --------------------------------------------------------------------------


@router.get("/email/recipients")
def recipients(
    q: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Buyers with an address, for the composer's picker.

    A convenience, not a restriction: the To field takes anything typed. This
    is here so a rep writing to someone already on file gets the send filed
    against them rather than stranded, which is the difference between a reply
    that comes home and one that lands unresolved.
    """
    rows = db.query(Lead).filter(Lead.email.is_not(None), Lead.email != "")
    if q:
        needle = f"%{q.lower()}%"
        rows = rows.filter(
            func.lower(Lead.email).like(needle) | func.lower(Lead.name).like(needle)
        )
    found = rows.order_by(Lead.created_at.desc()).limit(200).all()
    return {
        "recipients": [
            {"lead_id": lead.id, "name": lead.name or "", "email": lead.email}
            for lead in found
        ]
    }


class Compose(BaseModel):
    to: str
    subject: str
    body: str
    # Set when the rep pressed Reply on a message that already has a buyer.
    # Without it the address is put through the matcher, which is right for a
    # cold compose and wrong for a reply that arrived from a second address
    # nobody has on file yet.
    lead_id: str | None = None
    # The message being answered, so the buyer's client threads it under the
    # original instead of opening a second conversation in their inbox.
    in_reply_to_outreach_id: str | None = None


@router.post("/email/compose")
def compose(
    payload: Compose,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Write to anyone, from the dealership's address.

    Deliberately not restricted to buyers on file. A manager answering a
    stranger who wrote to sales@ is the case `/app/email` was built for, and a
    composer that could only reach existing leads would send them back to their
    own mail client -- where the reply is invisible to this system for good.

    What it will not do is skip `blocked_reason`. A composer is exactly where a
    rehearsal reaches a real prospect, so it goes through the same one guard as
    every other send; a refusal is recorded as a failed row and returned
    verbatim rather than raised, because the rep needs to see the sentence that
    names the setting.
    """
    to = (payload.to or "").strip()
    if not to or "@" not in to:
        raise HTTPException(400, "A recipient address is required.")
    if not (payload.subject or "").strip() and not (payload.body or "").strip():
        raise HTTPException(400, "An empty email is not worth sending.")

    lead = None
    if payload.lead_id:
        lead = db.query(Lead).filter_by(id=payload.lead_id).one_or_none()
        if lead is None:
            raise HTTPException(404, "No such buyer.")
    else:
        # The one matcher -- email exact, phone by its last ten digits, and a
        # name never. An address that belongs to nobody stays nobody's: the
        # send is still made and still listed here, it simply has no timeline
        # to sit on, and the composer says so before the rep presses send.
        lead = matching.match_lead(db, to, "")

    answering = None
    if payload.in_reply_to_outreach_id:
        answering = (
            db.query(Outreach).filter_by(id=payload.in_reply_to_outreach_id).one_or_none()
        )

    sender = get_email_sender()
    record = Outreach(
        lead_id=lead.id if lead else None,
        sent_by_user_id=user.id,
        channel="email",
        direction="out",
        kind="reply" if answering is not None else "manual",
        to_address=to,
        subject=(payload.subject or "").strip(),
        # The dealership's sign-off, appended here rather than typed. Stored on
        # the row as well as sent, so the timeline shows what actually went out
        # -- a body that reads differently on the buyer's page from what landed
        # in their inbox is the one thing a record must never do.
        # **This rep's own sign-off**, or the dealership's where they have not
        # written one. Stored on the row as well as sent -- a body that reads
        # differently on the buyer's page from what landed in their inbox is
        # the one thing a record must never do.
        body=outreach_send.with_signature(db, payload.body or "", user=user),
        provider=sender.name,
        status="queued",
        reply_token=outreach_send.mint_reply_token(db),
        in_reply_to=(answering.provider_message_id if answering else None) or None,
    )
    db.add(record)
    db.commit()

    blocked = outreach_send.blocked_reason(sender, to)
    if blocked:
        record.status = "failed"
        record.error = blocked
        db.commit()
        return {**outreach_out(record), "blocked": True}

    try:
        result = sender.send(
            to, record.subject, record.body,
            reply_to=outreach_send.reply_to_address(record.reply_token),
            in_reply_to=record.in_reply_to or "",
            from_address=outreach_send.dealership_from(db, sender),
            # The image half of this rep's sign-off. HTML only, because plain
            # text cannot carry a picture -- a sender that delivers text alone
            # ignores it and the reader still gets the words, which is the
            # right way for this to degrade. Built here rather than taken from
            # the request: it is markup going into somebody's inbox.
            html_tail=outreach_send.signature_html(db, user, str(request.base_url)),
        )
    except Exception as exc:  # NotConfigured, or anything the provider raised
        record.status = "failed"
        record.error = str(exc)
        db.commit()
        return {**outreach_out(record), "blocked": False}

    record.provider_message_id = result.message_id
    record.provider_thread_id = result.thread_id
    record.status = result.status
    record.error = result.detail if result.status != "sent" else ""
    record.sent_at = utcnow()
    db.commit()

    emit(db, "outreach.sent", {
        "outreach_id": record.id, "appointment_id": None,
        "lead_id": record.lead_id, "to": to, "provider": record.provider,
        "delivered_externally": sender.delivers, "conversation_id": None,
    })
    return {**outreach_out(record), "blocked": False}
