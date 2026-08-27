"""Liner's own dashboard, not a dealership's.

Everything here is about *our* customer -- a dealership evaluating Liner -- and
nothing here reads a buyer's record. That separation is the whole point of the
module: `/api/ops` is guarded by `require_owner`, and the accounts behind it
live in `ops_users` -- their own table, not a role on the dealership's. A
dealership's manager cannot reach any of this, and nothing here reads `leads`,
`conversations` or a recording.

Three things a two-person company actually needs: who asked for a demo and
when, the mail those people send, and to be told the moment a new one arrives
without being told again afterwards.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import outreach_send
from app.config import settings
from app.db import get_db, utcnow
from app.api.deps import require_owner
from app.events import emit
from app.integrations.base import NotConfigured
from app.integrations.registry import get_email_sender
from app.models import DemoRequest, InboundEmail, OpsUser
from app.schemas.serialize import iso

router = APIRouter(prefix="/ops", tags=["ops"])

#: What a demo request can be. `new` is the unread state and the only thing
#: that raises a notification -- opening one moves it to `seen`, which is what
#: makes the badge go away and stay away.
STATES = ("new", "seen", "done", "cancelled")


def _entry(row: DemoRequest) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "name": row.name,
        "dealership": row.dealership,
        "email": row.email,
        "phone": row.phone,
        "dealership_url": row.dealership_url,
        "message": row.message,
        "slot_at": iso(row.slot_at),
        "consented_at": iso(row.consent_at),
        # The words they agreed to, not just that they agreed. Shown on the
        # entry because that is the only place anyone would ever go looking.
        "consent_text": row.consent_text,
        "status": row.status,
        "unread": row.status == "new",
        "created_at": iso(row.created_at),
    }


def _identity(user: OpsUser):
    """Whose name goes on a message this person sends.

    One call, used by the composer and by the send, so the address shown and
    the address written cannot disagree. The rule itself lives in
    `outreach_send.identity_for`: a From has to be on the verified sending
    domain, and where it is not the fallback says why.

    Only the ops inbox uses it. A dealership's outreach is from the dealership
    rather than from a person -- and its Reply-To is the `reply+<token>@`
    address that routes an answer back into the buyer's timeline, which is not
    a header a rep's own address may take over.
    """
    return outreach_send.identity_for(get_email_sender(), user)


@router.get("/summary")
def summary(
    db: Session = Depends(get_db), user: OpsUser = Depends(require_owner)
) -> dict:
    """The three numbers the nav needs, in one call.

    Unread is the notification count and nothing else: a demo somebody has
    opened is not news any more, however recently it arrived.
    """
    sender = get_email_sender()
    identity = _identity(user)
    now = utcnow()
    upcoming = (
        db.query(DemoRequest)
        .filter(
            DemoRequest.slot_at.isnot(None),
            DemoRequest.slot_at >= now,
            DemoRequest.status != "cancelled",
        )
        .count()
    )
    return {
        "unread": db.query(DemoRequest).filter(DemoRequest.status == "new").count(),
        "upcoming": upcoming,
        "unmatched_mail": db.query(InboundEmail)
        .filter(InboundEmail.outcome == "unresolved")
        .count(),
        "support_email": settings.support_email,
        "founder_email": settings.founder_email,
        # Computed here rather than in the page, by the same function the send
        # uses -- a composer that promises one address while the send writes
        # another is a lie nobody would ever catch.
        "reply_to": identity.reply_to,
        "from_address": identity.from_address,
        #: True when mail really leaves under this person's own name.
        "from_is_personal": identity.personal,
        #: Why it does not, when it does not. Shown on the composer, because
        #: this is the one thing about a send somebody can actually fix.
        "from_note": identity.note,
        "sender": sender.name,
        "sender_delivers": sender.delivers,
        "timezone": settings.demo_timezone,
    }


@router.get("/demos")
def list_demos(
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Every demo and support request, newest first.

    A window narrows it to what the calendar is drawing. Support requests have
    no slot, so they are never inside a window -- and are returned unfiltered,
    because a message with no time attached still has to be somewhere.
    """
    query = db.query(DemoRequest)
    rows = query.order_by(DemoRequest.created_at.desc()).limit(500).all()
    if start or end:
        rows = [
            r for r in rows
            if r.slot_at is None
            or ((start is None or r.slot_at >= start.replace(tzinfo=None))
                and (end is None or r.slot_at < end.replace(tzinfo=None)))
        ]
    return {"requests": [_entry(r) for r in rows]}


@router.get("/demos/{request_id}")
def get_demo(
    request_id: str,
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    row = db.query(DemoRequest).filter_by(id=request_id).one_or_none()
    if row is None:
        raise HTTPException(404, "No such request")
    return _entry(row)


class StatusBody(BaseModel):
    status: str


@router.post("/demos/{request_id}/status")
def set_status(
    request_id: str,
    body: StatusBody,
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Mark one read, done, or cancelled.

    Reading is what clears the notification, and it is a state on the row
    rather than a per-person flag: there are two of us, and "I have seen it"
    from either is the answer the other needs too. A read receipt per user
    would make the badge argue with itself across two laptops.
    """
    if body.status not in STATES:
        raise HTTPException(400, f"status must be one of {', '.join(STATES)}")
    row = db.query(DemoRequest).filter_by(id=request_id).one_or_none()
    if row is None:
        raise HTTPException(404, "No such request")
    was = row.status
    row.status = body.status
    db.commit()
    if was != row.status:
        emit(db, "demo.updated", {
            "request_id": row.id, "status": row.status, "by": user.id,
        })
    return _entry(row)


@router.get("/mail")
def inbox(
    box: str = Query("all"),
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Mail addressed to us, which is a different pile from the dealership's.

    Two sources, deliberately joined here rather than in a table: the forms on
    the marketing site (`ops_demo_requests`) and anything that arrived at the
    inbound endpoint without resolving to a buyer -- which is what a stranger
    writing to `support@` looks like. The dealership's mailbox shows the other
    half: replies that *did* resolve to one of their buyers.
    """
    rows: list[dict] = []
    for request in (
        db.query(DemoRequest).order_by(DemoRequest.created_at.desc()).limit(300).all()
    ):
        rows.append({
            "id": request.id,
            "source": "form",
            "kind": request.kind,
            "from_name": request.name,
            "from_address": request.email,
            "subject": (
                f"Demo request -- {request.dealership or request.name}"
                if request.kind == "demo"
                else f"Support -- {request.name}"
            ),
            "body": request.message or _demo_body(request),
            "at": iso(request.created_at),
            "unread": request.status == "new",
            "status": request.status,
            "slot_at": iso(request.slot_at),
            "phone": request.phone,
            "dealership": request.dealership,
            "dealership_url": request.dealership_url,
        })
    for mail in (
        db.query(InboundEmail)
        .filter(InboundEmail.outcome == "unresolved")
        .order_by(InboundEmail.created_at.desc())
        .limit(300)
        .all()
    ):
        rows.append({
            "id": mail.id,
            "source": "email",
            "kind": "unmatched",
            "from_name": "",
            "from_address": mail.from_address,
            "subject": mail.subject or "(no subject)",
            "body": mail.body or "",
            "at": iso(mail.created_at),
            # An unresolved delivery has no read state of its own -- there is
            # no column for one and it is not worth a migration. It is shown in
            # its own box instead, which is where somebody goes looking.
            "unread": False,
            "status": mail.outcome,
            "slot_at": None,
            "phone": "",
            "dealership": "",
            "dealership_url": "",
        })

    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    boxes = {
        "all": lambda r: True,
        "demos": lambda r: r["kind"] == "demo",
        "support": lambda r: r["kind"] == "support",
        "unmatched": lambda r: r["kind"] == "unmatched",
        "unread": lambda r: r["unread"],
    }
    if box not in boxes:
        raise HTTPException(400, f"box must be one of {', '.join(boxes)}")
    # Counted from the same predicates the filter uses, so a box saying 12
    # cannot show 9 -- the mistake the dealership's mailbox already made once.
    counts = {name: sum(1 for r in rows if match(r)) for name, match in boxes.items()}
    return {"box": box, "counts": counts, "messages": [r for r in rows if boxes[box](r)]}


def _demo_body(request: DemoRequest) -> str:
    when = (
        request.slot_at.strftime("%A %-d %B at %-I:%M %p")
        if request.slot_at else "no time picked"
    )
    lines = [
        f"{request.name} at {request.dealership or 'an unnamed dealership'} booked a demo.",
        f"When: {when} ({settings.demo_timezone})",
        f"Email: {request.email}",
        f"Phone: {request.phone or 'not given'}",
    ]
    if request.dealership_url:
        lines.append(f"Site: {request.dealership_url}")
    return "\n".join(lines)


class ReplyBody(BaseModel):
    to: str
    subject: str
    body: str


@router.post("/mail/reply")
def reply(
    body: ReplyBody,
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Write from the ops inbox -- an answer, or a first message.

    Both, through one handler. Reaching a dealership we want to talk to is the
    same act as answering one that wrote in, and a second endpoint for it is
    how one of the two stops going through `blocked_reason`.

    Through the same sender and the same outbound limit as everything else --
    `OUTBOUND_ONLY_TO` is exactly as load-bearing here as it is on a dealer's
    composer, and a reply typed to a real prospect from a rehearsal is the
    failure it exists to stop.

    Under the sender's own name where the deployment can prove it owns the
    address, and back to them either way. Two people share this inbox: a reply
    that always came from `support@` read like a ticket, and one that always
    came back to the founder sent half the answers to the wrong person.
    """
    to = (body.to or "").strip()
    if "@" not in to:
        raise HTTPException(400, "That does not look like an email address.")
    sender = get_email_sender()
    identity = _identity(user)
    blocked = outreach_send.blocked_reason(sender, to)
    if blocked:
        return {"sent": False, "reason": blocked}

    try:
        result = sender.send(
            to=to,
            subject=(body.subject or "").strip() or "Liner AI",
            body=body.body or "",
            reply_to=identity.reply_to,
            from_address=identity.from_address,
        )
    except NotConfigured as exc:
        return {"sent": False, **exc.as_dict()}
    return {
        "sent": result.status == "sent",
        "status": result.status,
        "provider": sender.name,
        "from_address": identity.from_address,
        "from_is_personal": identity.personal,
        "from_note": identity.note,
        "reply_to": identity.reply_to,
        "detail": result.detail or "",
    }
