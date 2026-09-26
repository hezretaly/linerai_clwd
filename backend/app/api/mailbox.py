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

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import email_envelopes, email_outbound, matching, outreach_send, outreach_status
from app.api.deps import current_user
from app.api.inbound_email import signature_for
from app.config import settings
from app.db import get_db, utcnow
from app import email_agent, flags, profile
from app.email_intake import is_ours
from app import email_threads
from app.email_threads import threads as email_threads_for
from app.events import emit
from app.schemas.serialize import iso, stamp
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
        # This dealership's, which is its own where its profile names one.
        "reply_domain": profile.mail_domain(),
        "endpoint": "/api/inbound-email",
        "signature_header": "X-Liner-Signature",
    }


@router.get("/email/messages")
def messages(
    box: str = "all",
    q: str = "",
    window: str | None = Query(None),
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
    rows = db.query(Outreach).filter(Outreach.channel == "email")
    if window:
        if window != "24h":
            raise HTTPException(400, "window must be '24h'")
        since = utcnow() - timedelta(hours=24)
        rows = rows.filter(outreach_status.SENT_AT >= since)
    rows = rows.order_by(Outreach.created_at.desc()).limit(CEILING).all()
    lead_ids = {r.lead_id for r in rows if r.lead_id}
    leads = {
        lead.id: lead
        for lead in (
            db.query(Lead).filter(Lead.id.in_(lead_ids)).all() if lead_ids else []
        )
    }

    now = utcnow()
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
            # One word for what actually happened -- 'sent'/'sending'/
            # 'not_sent' (an inbound row is always 'received'), from
            # `app/outreach_status.py`. `_in_box` reads this rather than the
            # raw status, so a send still in flight is neither Sent nor red
            # 'Not sent' while the provider call is in progress, and a row
            # stuck at 'queued' past `STUCK_AFTER` (a crash mid-send) does not
            # sit in Sent forever.
            "delivery": outreach_status.delivery(r.direction, r.status, r.created_at, now=now),
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
    # `email_threads.unplaced` -- the one query for "mail nobody could place"
    # -- rather than a second copy of it here. The two used to disagree in
    # two ways: this one repeated the `is_ours` filter inline instead of
    # sharing it, and (separately, see `email_threads.threads()`) the
    # People tab's own copy silently capped itself at 200 rows before
    # counting, which this one never did (item 47).
    for r in email_threads.unplaced(db, limit=CEILING):
        # `unplaced()` is shared with `threads()`'s own People-tab copy and
        # carries no time bound of its own -- so without this, `window=24h`
        # narrowed every sent row above but let an unmatched stranger's mail
        # from any age keep showing under `all`/`unmatched`, silently wider
        # than the window the banner claims.
        if window and r.created_at < since:
            continue
        out.append({
            "id": r.id,
            "kind": "unmatched",
            "direction": "in",
            "address": r.from_address,
            "subject": r.subject,
            "body": r.body,
            "status": "unmatched",
            "delivery": "received",
            "error": "",
            "provider": "inbound",
            "delivered_externally": True,
            "lead_id": None,
            "lead_name": "",
            "at": stamp(r.created_at),
        })

    out.sort(key=lambda m: m["at"] or "", reverse=True)

    # `_in_view` is `_in_box` *and* the search -- both the tab totals and the
    # list are built from it, so `counts[box]` always equals `matching` for
    # whatever `q` is. Before this, `counts` read `_in_box` alone and the list
    # applied the search on top, so a search narrowed the rows shown under a
    # tab while the tab itself kept advertising the unsearched total: "Sent
    # 24" over three rows, "All 44" over "Nothing matches that search."
    counts = {
        key: sum(1 for m in out if _in_view(m, key, q))
        for key in ("all", "received", "sent", "failed", "unmatched")
    }
    shown = [m for m in out if _in_view(m, box, q)]

    # A page, and the honest size of what it came from. Returning a slice while
    # the tab counted every row is how a box said 230 and listed 200 -- the
    # same "says 12, shows 9" bug `_in_box` exists to prevent, arriving through
    # the back door of a silent cap. The count stays the true total, because a
    # manager asking how much mail there is wants the answer; the list says how
    # far down it goes.
    start = max(offset, 0)
    end = start + max(min(limit, CEILING), 1)
    page = shown[start:end]
    _with_envelopes(db, page, {r.id: r for r in rows})
    return {
        "messages": page,
        "counts": counts,
        "matching": len(shown),
        "offset": start,
        "has_more": end < len(shown),
    }


def _with_envelopes(db: Session, page: list[dict], outreach_by_id: dict[str, Outreach]) -> None:
    """Put `email` -- every recipient, the files, importance -- on the rows
    actually being returned.

    For the page, not the whole mailbox: the counts are computed from up to
    `CEILING` rows, and loading envelopes and files for five thousand messages
    to show a hundred of them is work nobody sees. Batched either way, so a
    page costs a fixed handful of queries rather than one per row. `address`
    stays the one string it always was; the lists are the new key.
    """
    placed = [outreach_by_id[m["id"]] for m in page if m["kind"] == "message" and m["id"] in outreach_by_id]
    receipts = [m["id"] for m in page if m["kind"] == "unmatched"]
    by_outreach = email_envelopes.for_outreach_many(db, placed)
    by_receipt = email_envelopes.for_receipts_many(db, receipts)
    files = email_envelopes.attachments_of(
        db, [e.id for e in [*by_outreach.values(), *by_receipt.values()]]
    )
    for m in page:
        env = (by_outreach if m["kind"] == "message" else by_receipt).get(m["id"])
        m["email"] = email_envelopes.summary(
            env, files.get(env.id, []) if env else [], include_bcc=True,
        )


def _in_box(m: dict, box: str) -> bool:
    """One definition of each box, used for the counts and for the filtering.
    Two copies is how a tab ends up saying 12 and showing 9."""
    if box == "received":
        return m["direction"] == "in" and m["kind"] == "message"
    if box == "sent":
        return m["delivery"] == "sent"
    # Refusals live here: an allow-list block is a send that did not happen,
    # and it is the one a manager has to notice. A send still in flight
    # (`delivery == "sending"`) is neither Sent nor Failed -- it turns into
    # one or the other once the provider answers, or after `STUCK_AFTER` if
    # it never does.
    if box == "failed":
        return m["direction"] == "out" and m["delivery"] == "not_sent"
    if box == "unmatched":
        return m["kind"] == "unmatched"
    return True


def _matches(m: dict, q: str) -> bool:
    return not q or q.lower() in f"{m['address']} {m['subject']} {m['body']} {m['lead_name']}".lower()


def _in_view(m: dict, box: str, q: str) -> bool:
    """Tab membership under the current search -- the counts and the list
    both, so a tab's own number never disagrees with what it shows under it."""
    return _in_box(m, box) and _matches(m, q)


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
    # `have_model`'s own wording names LLM_MODE on purpose -- it is shared
    # with the "Draft with Liner" composer, where a rep needs a refusal a
    # stub reply cannot be mistaken for. This card has a different reader (a
    # dealership manager deciding whether to turn email on), so its "no
    # model" reason is reworded here rather than in the shared function.
    detail = verdict.detail
    if verdict.reason == "no_model":
        detail = (
            "Liner isn't connected to a live assistant on this deployment "
            "yet, so it has nothing to write email replies with. Contact "
            "Liner to set this up."
        )
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
        "detail": detail,
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
        # The true total -- computed before the preview below is sliced to 20,
        # so `AgentSwitch.tsx`'s "Queued (N)" card is never `min(true_count, 20)`
        # wearing the true count's own label. Before this the card's only
        # source was `len(waiting)`, the 20-row preview itself, so more than 20
        # replies genuinely queued (a realistic hour: the default cooldown is
        # 60 minutes and the hourly ceiling is 30) silently capped the number
        # a rep saw at 20 (item 48).
        "waiting_count": (
            db.query(EmailReplyDue).filter(EmailReplyDue.state == "waiting").count()
        ),
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
def email_threads_view(
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
    say 12 and show 9. **Open** is the default because it is the working list.
    `graduated` is the rest -- buyers who are also on `/app/conversations`,
    read from the same predicate that list uses (`threads.started_leads`),
    not a separate exchange threshold: this page used to say "Conversations 0"
    over 24 buyers who were on `/app/conversations` from their first email,
    because the threshold governed presentation here and nothing governed
    membership there. See `app.email_threads`'s module docstring.
    """
    rows = email_threads_for(db)  # uncapped -- see email_threads.threads()
    tab_counts = email_threads.counts(rows)
    return {
        "threads": [
            {**r, "at": stamp(r["at"]), "last_body": (r["last_body"] or "")[:280]}
            for r in rows if email_threads.in_box(r, box)
        ][:200],
        "counts": tab_counts,
    }


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
    try:
        message = email_outbound.build(
            db, to=body.to, subject="Liner test message",
            body="This is a test from the Liner dashboard. Replying to it proves the "
                 "round trip works: the reply address on this message routes back "
                 "into the system.",
        )
    except email_outbound.OutboundError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    # No event: a test is not outreach to anybody, and a dashboard reacting to
    # it as though a buyer had been written to would be the one false signal
    # on the page.
    sent = email_outbound.send(
        db, message, kind="test", lead_id=None, sent_by_user_id=user.id,
        announce_event=False,
    )
    if sent.result is None:
        return {"status": "failed", "error": sent.detail, "provider": sent.sender.name}
    return {
        "status": sent.result.status,
        "error": sent.result.detail,
        "provider": sent.result.provider,
        # Without a domain there is no Reply-To at all, which is worth saying:
        # the mail may go out and still be unreplyable.
        "reply_to": outreach_send.reply_to_address(sent.record.reply_token or ""),
    }


class TestInbound(BaseModel):
    outreach_id: str


@router.post("/email/test-inbound")
def test_inbound(
    body: TestInbound,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Post a signed sample through the live handler.

    Deliberately over HTTP to our own endpoint rather than calling `receive()`
    in-process: the signature check and the header plumbing are exactly the
    parts that break, and a test that skipped them would pass while real
    deliveries 401.

    **To this process, at the address it is listening on** -- not a
    hardcoded `127.0.0.1:8000`. Two instances share one box (production on
    8000, the demo on 8001), and the demo's test reply went to production's
    intake: refused only because the two secrets differ.
    """
    import json

    import httpx

    sent = db.query(Outreach).filter_by(id=body.outreach_id).one_or_none()
    if sent is None or not sent.reply_token:
        raise HTTPException(404, "No such send, or it carries no reply token.")

    domain = profile.mail_domain() or "example.invalid"
    # What a real reply would quote back: the Message-ID the send went out
    # with, where one is known. A provider's own id is what this used to
    # send, and no mail client would ever have put that in a header.
    parent = email_outbound.thread_under_outreach(db, sent).in_reply_to
    payload = {
        "messageId": f"<test-{sent.reply_token}-{utcnow().isoformat()}>",
        "from": sent.to_address,
        "to": f"reply+{sent.reply_token}@{domain}",
        "subject": f"Re: {sent.subject}",
        "text": "This is a test reply posted from the Liner dashboard.",
        "inReplyTo": parent or sent.provider_message_id or "",
        "receivedAt": utcnow().isoformat(),
    }
    raw = json.dumps(payload).encode()
    try:
        host, port = (request.scope.get("server") or ("127.0.0.1", 8000))[:2]
        response = httpx.post(
            f"http://{host}:{port}/api/inbound-email",
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
    # `Lead.has_email` -- not `email.is_not(None), email != ""` -- so this
    # picker agrees with `/reach` (which strips before checking) and with
    # `campaigns.py`'s audiences (which, before item 38, did not check email
    # at all): a whitespace-only address used to pass this filter and fail
    # `/reach`, and a facebook-sourced lead with `email=""` never reached
    # this picker while still counting toward a campaign card's "ready to
    # run" audience.
    rows = db.query(Lead).filter(Lead.has_email)
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


#: What a recipient field accepts: the one string every composer used to send
#: -- which may itself hold several addresses, comma or semicolon separated --
#: or a list of strings or `{name, address}` objects.
Addresses = str | list[str | dict] | None


class ForwardOf(BaseModel):
    #: `message` (an `outreach` row) or `unmatched` (a delivery nobody placed).
    kind: str
    id: str


class Compose(BaseModel):
    to: Addresses
    cc: Addresses = None
    bcc: Addresses = None
    subject: str = ""
    #: The text half. Ignored when `html` is given: the text is then written
    #: from the HTML, so the two halves cannot say different things.
    body: str = ""
    #: The rep's formatted body, from the rich editor. Cleaned to the
    #: composer's allowlist before anything is stored or sent.
    html: str = ""
    #: Uploads from `POST /api/email/attachments`, and files of a message
    #: being forwarded.
    attachment_ids: list[str] | None = None
    #: normal | high.
    importance: str = "normal"
    # Set when the rep pressed Reply on a message that already has a buyer.
    # Without it the address is put through the matcher, which is right for a
    # cold compose and wrong for a reply that arrived from a second address
    # nobody has on file yet.
    lead_id: str | None = None
    # The message being answered, so the buyer's client threads it under the
    # original instead of opening a second conversation in their inbox.
    in_reply_to_outreach_id: str | None = None
    # The same, for a delivery nobody could place -- a stranger who wrote to
    # sales@ has a receipt and no outreach row.
    in_reply_to_receipt_id: str | None = None
    # Forwarding: whose files come along. With no `attachment_ids` every file
    # it carried is attached; with them, exactly those -- the rep may have
    # taken some off.
    forward_of: ForwardOf | None = None


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
    every other send -- over every To, Cc and Bcc -- and a refusal is recorded
    as a failed row and returned verbatim rather than raised, because the rep
    needs to see the sentence that names the setting.

    Reply, reply-all and forward are all this endpoint: a reply names the
    message it answers (`in_reply_to_outreach_id`, or `in_reply_to_receipt_id`
    for mail nobody placed) so it threads under it; a forward names the
    message whose files come along. Which addresses go in To and Cc is the
    composer's to fill in -- the reader offers them -- and this checks and
    sends what it is given.
    """
    lead = None
    if payload.lead_id:
        lead = db.query(Lead).filter_by(id=payload.lead_id).one_or_none()
        if lead is None:
            raise HTTPException(404, "No such buyer.")

    answering = None
    if payload.in_reply_to_outreach_id:
        answering = (
            db.query(Outreach).filter_by(id=payload.in_reply_to_outreach_id).one_or_none()
        )
    receipt = None
    if payload.in_reply_to_receipt_id:
        receipt = _unplaced(db, payload.in_reply_to_receipt_id)
    forwarded = _forwarded_files(db, payload.forward_of) if payload.forward_of else []

    # The message it answers decides where it threads -- its Message-ID, never
    # a provider's id, which is what used to go in this header.
    thread = (
        email_outbound.thread_under_outreach(db, answering) if answering is not None
        else email_outbound.thread_under_receipt(db, receipt)
    )
    try:
        message = email_outbound.build(
            db,
            to=payload.to, cc=payload.cc, bcc=payload.bcc,
            subject=payload.subject, body=payload.body, html=payload.html,
            attachment_ids=(
                payload.attachment_ids if payload.attachment_ids is not None else forwarded
            ),
            importance=payload.importance,
            thread=thread,
            # **This rep's own sign-off**, or their name over the dealership's
            # where they have not written one, appended here rather than
            # typed. Stored on the row as well as sent -- a body that reads
            # differently on the buyer's page from what landed in their inbox
            # is the one thing a record must never do. The image half rides
            # the HTML only, built from a token we minted, never from the
            # request: it is markup going into somebody's inbox.
            sign=True, signer=user, base_url=str(request.base_url),
            uploader_id=user.id,
        )
    except email_outbound.OutboundError as exc:
        raise HTTPException(exc.status, str(exc)) from None

    if lead is None:
        # The one matcher -- email exact, phone by its last ten digits, and a
        # name never -- on the first To. An address that belongs to nobody
        # stays nobody's: the send is still made and still listed here, it
        # simply has no timeline to sit on, and the composer says so before
        # the rep presses send. A reply to a buyer's own message stays on
        # their timeline even from an address the matcher does not know.
        lead = matching.match_lead(db, message.primary, "") or (
            db.query(Lead).filter_by(id=answering.lead_id).one_or_none()
            if answering is not None and answering.lead_id else None
        )

    kind = (
        "forward" if payload.forward_of
        else "reply" if (answering is not None or receipt is not None)
        else "manual"
    )
    sent = email_outbound.send(
        db, message, kind=kind, lead_id=lead.id if lead else None,
        sent_by_user_id=user.id,
    )
    return {**sent.out(), "blocked": bool(sent.blocked)}


def _unplaced(db: Session, receipt_id: str) -> InboundEmail:
    """A delivery a rep may answer from the mailbox, or a 404.

    The same rule the list follows: mail addressed to *us* is Liner's, listed
    at `/ops`, and not on a dealership's page to read or to answer.
    """
    row = db.query(InboundEmail).filter_by(id=receipt_id).one_or_none()
    if row is None or is_ours(row.to_address):
        raise HTTPException(404, "No such message.")
    return row


def _forwarded_files(db: Session, of: ForwardOf) -> list[str]:
    """Every file the forwarded message carried, by id, or a 404.

    Only files with their bytes: one that was refused on arrival has nothing
    to send, and listing it would make the forward fail on a file the rep
    never chose. Inline images are part of the body they came in, which the
    composer's HTML cannot carry, so they are left behind with it.
    """
    if of.kind == "message":
        row = db.query(Outreach).filter_by(id=of.id).one_or_none()
        if row is None or row.channel != "email":
            raise HTTPException(404, "No such message.")
        env = email_envelopes.for_outreach(db, row)
    elif of.kind == "unmatched":
        env = email_envelopes.for_receipt(db, _unplaced(db, of.id).id)
    else:
        raise HTTPException(400, "forward_of.kind is 'message' or 'unmatched'.")
    if env is None:
        return []
    return [
        a.id for a in email_envelopes.attachments_of(db, [env.id]).get(env.id, [])
        if a.path and not a.refused and not (a.disposition == "inline" and a.content_id)
    ]
