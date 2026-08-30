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
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import outreach_send
from app.config import settings
from app.db import get_db, utcnow
from app.api.deps import require_owner
from app.events import emit
from app.integrations.base import NotConfigured
from app.integrations.registry import get_email_sender
from app.models import (
    DemoRequest,
    InboundEmail,
    OpsMailState,
    OpsMessage,
    OpsUser,
)
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

    Where it falls back, it falls back to **Liner** and never to a dealership.
    `SENDING_FROM` used to carry a display name and `.env.example` illustrated
    it with "Riverside Auto", so our own support replies went out signed as a
    fixture car dealership -- and would have gone out signed as the reader's
    own dealership the moment somebody put a real name there, which is worse.
    """
    return outreach_send.identity_for(
        get_email_sender(), user, fallback_name=outreach_send.OPS_SENDER_NAME,
    )


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


def _states(db: Session) -> dict[tuple[str, str], OpsMailState]:
    """Read and trash marks, keyed the way the rows are."""
    return {(row.kind, row.ref_id): row for row in db.query(OpsMailState).all()}


def _state_row(db: Session, kind: str, ref_id: str) -> OpsMailState:
    row = (
        db.query(OpsMailState)
        .filter(OpsMailState.kind == kind, OpsMailState.ref_id == ref_id)
        .one_or_none()
    )
    if row is None:
        row = OpsMailState(kind=kind, ref_id=ref_id)
        db.add(row)
    return row


def _inbound(db: Session) -> list[dict]:
    """The two sources of mail addressed to us, in one shape.

    Joined here rather than in a table: the forms on the marketing site
    (`ops_demo_requests`) and anything that arrived at the inbound endpoint
    without resolving to a buyer -- which is what a stranger writing to
    `support@` looks like. The dealership's mailbox shows the other half.
    """
    marks = _states(db)
    rows: list[dict] = []

    for request in (
        db.query(DemoRequest).order_by(DemoRequest.created_at.desc()).limit(300).all()
    ):
        mark = marks.get(("form", request.id))
        rows.append({
            "id": request.id,
            "source": "form",
            "kind": request.kind,
            "direction": "in",
            "from_name": request.name,
            "from_address": request.email,
            "to_address": "",
            "subject": (
                f"Demo request -- {request.dealership or request.name}"
                if request.kind == "demo"
                else f"Support -- {request.name}"
            ),
            "body": request.message or _demo_body(request),
            "at": iso(request.created_at),
            # `status` is already this fact and the notification bell reads
            # it. A second copy in ops_mail_state is how the bell and the
            # mailbox start disagreeing about the same message.
            "unread": request.status == "new",
            "status": request.status,
            "trashed": bool(mark and mark.trashed_at),
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
        mark = marks.get(("email", mail.id))
        rows.append({
            "id": mail.id,
            "source": "email",
            "kind": "unmatched",
            "direction": "in",
            "from_name": "",
            "from_address": mail.from_address,
            "to_address": mail.to_address or "",
            "subject": mail.subject or "(no subject)",
            "body": mail.body or "",
            "at": iso(mail.created_at),
            # Unread until somebody opens it. This was hardcoded False,
            # because `inbound_emails` has no column for it and there is no
            # Alembic here -- so every delivery arrived looking already read,
            # which is the opposite of what an inbox is for. The mark lives in
            # its own table, which a database that already exists does get.
            "unread": not (mark and mark.read_at),
            "status": mail.outcome,
            "trashed": bool(mark and mark.trashed_at),
            "slot_at": None,
            "phone": "",
            "dealership": "",
            "dealership_url": "",
        })
    return rows


def _outbound(db: Session, user: OpsUser) -> list[dict]:
    """What we wrote: drafts still being written, and what has gone out.

    Drafts are the author's own -- an unfinished message is not something to
    put in front of somebody else -- while Sent is shared, because "has anyone
    answered these people yet" is the question two people sharing an inbox
    actually ask.
    """
    rows = []
    for msg in (
        db.query(OpsMessage)
        .filter(
            or_(OpsMessage.state != "draft", OpsMessage.author_id == user.id),
        )
        .order_by(OpsMessage.created_at.desc())
        .limit(300)
        .all()
    ):
        author = db.query(OpsUser).filter_by(id=msg.author_id).one_or_none()
        rows.append({
            "id": msg.id,
            "source": "ours",
            "kind": msg.state,
            "direction": "out",
            "from_name": author.name if author else "",
            "from_address": msg.from_address or (author.email if author else ""),
            "to_address": msg.to_address,
            "subject": msg.subject or "(no subject)",
            "body": msg.body or "",
            "at": iso(msg.sent_at or msg.updated_at or msg.created_at),
            # Nothing we wrote is ever unread -- we wrote it. A Sent box that
            # accrues an unread count is a mailbox arguing with itself.
            "unread": False,
            "status": msg.state,
            "trashed": bool(msg.trashed_at),
            "provider": msg.provider,
            "detail": msg.detail,
            "reply_to": msg.reply_to,
            "author": author.name if author else "",
            "mine": msg.author_id == user.id,
            "slot_at": None,
            "phone": "",
            "dealership": "",
            "dealership_url": "",
        })
    return rows


#: Every box, defined exactly once, for the counts and the filter both. Two
#: copies is how a tab says 12 and shows 9 -- the mistake the dealership's
#: mailbox already made.
BOXES = {
    "all": lambda r: r["direction"] == "in" and not r["trashed"],
    "unread": lambda r: r["direction"] == "in" and not r["trashed"] and r["unread"],
    "demos": lambda r: r["kind"] == "demo" and not r["trashed"],
    "support": lambda r: r["kind"] == "support" and not r["trashed"],
    "unmatched": lambda r: r["kind"] == "unmatched" and not r["trashed"],
    "drafts": lambda r: r["kind"] == "draft" and not r["trashed"],
    "sent": lambda r: r["kind"] in ("sent", "failed") and not r["trashed"],
    # The one box defined by the mark rather than the source, so a discarded
    # draft and a deleted form land in the same place a person looks.
    "trash": lambda r: r["trashed"],
}


@router.get("/mail")
def inbox(
    box: str = Query("all"),
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """The whole mailbox: what arrived, what we wrote, and what was binned."""
    if box not in BOXES:
        raise HTTPException(400, f"box must be one of {', '.join(BOXES)}")
    rows = _inbound(db) + _outbound(db, user)
    rows.sort(key=lambda r: r["at"] or "", reverse=True)
    counts = {name: sum(1 for r in rows if match(r)) for name, match in BOXES.items()}
    return {"box": box, "counts": counts, "messages": [r for r in rows if BOXES[box](r)]}


class MarkBody(BaseModel):
    #: form | email | ours
    kind: str
    id: str


class ReadBody(MarkBody):
    read: bool = True


class TrashBody(MarkBody):
    trashed: bool = True


def _target(db: Session, body: MarkBody):
    if body.kind == "ours":
        row = db.query(OpsMessage).filter_by(id=body.id).one_or_none()
    elif body.kind == "form":
        row = db.query(DemoRequest).filter_by(id=body.id).one_or_none()
    elif body.kind == "email":
        row = db.query(InboundEmail).filter_by(id=body.id).one_or_none()
    else:
        raise HTTPException(400, "kind must be form, email or ours")
    if row is None:
        raise HTTPException(404, "No such message")
    return row


@router.post("/mail/read")
def mark_read(
    body: ReadBody,
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Opening a message reads it; the button is for putting it back.

    Reading is still done by opening the thing rather than by pressing
    something -- a notification left sitting after it has been read is one
    people learn to ignore. What this adds is the other direction: marking
    something unread on purpose is a person saying "I have not dealt with this
    yet", which is the only way an inbox can be used as a queue.

    Forms answer from `status`, because the bell reads that and two copies of
    one fact is how the two start disagreeing. Everything else answers from
    `ops_mail_state`.
    """
    row = _target(db, body)
    if body.kind == "form":
        row.status = "seen" if body.read else "new"
    elif body.kind == "email":
        _state_row(db, "email", body.id).read_at = utcnow() if body.read else None
    # Our own messages are never unread; marking one is a no-op rather than an
    # error, so a client can send the same call for every row.
    db.commit()
    return {"ok": True, "kind": body.kind, "id": body.id, "read": body.read}


@router.post("/mail/trash")
def mark_trashed(
    body: TrashBody,
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Trash is a timestamp, and Restore is the same call with false.

    Never a delete. A message somebody wrote, or a demo somebody booked, is
    the last thing to destroy on their behalf -- and a Trash that cannot be
    undone is a delete button wearing a friendlier word.
    """
    row = _target(db, body)
    when = utcnow() if body.trashed else None
    if body.kind == "ours":
        row.trashed_at = when
    else:
        _state_row(db, body.kind, body.id).trashed_at = when
    db.commit()
    return {"ok": True, "kind": body.kind, "id": body.id, "trashed": body.trashed}


class DraftBody(BaseModel):
    #: Present when updating one that already exists.
    id: str | None = None
    to: str = ""
    subject: str = ""
    body: str = ""
    reply_to_kind: str = ""
    reply_to_id: str = ""


@router.post("/mail/draft")
def save_draft(
    body: DraftBody,
    db: Session = Depends(get_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Keep an unfinished message.

    The dealership's composer deliberately has no Drafts tab, because nothing
    there stores one -- it is built from the lead's state and lives in the
    browser until send. This is the other case: a first message to somebody we
    want to talk to is written over a morning, and a browser tab is not where
    that should live.

    An empty draft is not saved. A row with nothing in it is a Drafts box that
    fills with ghosts every time somebody opens the composer and changes their
    mind.
    """
    if not (body.to.strip() or body.subject.strip() or body.body.strip()):
        raise HTTPException(400, "Nothing to save yet.")
    if body.id:
        draft = db.query(OpsMessage).filter_by(id=body.id).one_or_none()
        if draft is None:
            raise HTTPException(404, "No such draft")
        if draft.author_id != user.id:
            raise HTTPException(403, "That draft is somebody else's.")
        if draft.state != "draft":
            raise HTTPException(409, "That message has already been sent.")
    else:
        draft = OpsMessage(author_id=user.id, state="draft")
        db.add(draft)
    draft.to_address = body.to.strip()
    draft.subject = body.subject.strip()
    draft.body = body.body
    draft.reply_to_kind = body.reply_to_kind
    draft.reply_to_id = body.reply_to_id
    draft.updated_at = utcnow()
    db.commit()
    db.refresh(draft)
    return {"id": draft.id, "state": draft.state, "updated_at": iso(draft.updated_at)}


class ReplyBody(BaseModel):
    to: str
    subject: str
    body: str
    #: The draft this is being sent from, if it was written as one.
    draft_id: str | None = None
    reply_to_kind: str = ""
    reply_to_id: str = ""


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


@router.post("/mail/reply")
@router.post("/mail/send")
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

    # The row for this message exists before the send is attempted, and it is
    # the draft's own row when it was written as one. Minting a new row on
    # send would leave the draft sitting in Drafts as well, so one message a
    # person wrote would be two rows in two boxes.
    message = None
    if body.draft_id:
        message = db.query(OpsMessage).filter_by(id=body.draft_id).one_or_none()
        if message is not None and message.author_id != user.id:
            raise HTTPException(403, "That draft is somebody else's.")
    if message is None:
        message = OpsMessage(author_id=user.id)
        db.add(message)
    message.to_address = to
    message.subject = (body.subject or "").strip()
    message.body = body.body or ""
    message.reply_to_kind = body.reply_to_kind or message.reply_to_kind
    message.reply_to_id = body.reply_to_id or message.reply_to_id
    message.from_address = identity.from_address
    message.reply_to = identity.reply_to
    message.provider = sender.name

    def _record(state: str, detail: str, provider_id: str = "") -> None:
        """What happened, kept whatever it was.

        A refused send stays as `failed` rather than being discarded: it is
        the one a person most needs to find again, and dropping the row loses
        what they typed along with it.
        """
        message.state = state
        message.detail = detail
        message.provider_message_id = provider_id
        message.updated_at = utcnow()
        message.sent_at = utcnow() if state == "sent" else None
        db.commit()

    blocked = outreach_send.blocked_reason(sender, to)
    if blocked:
        _record("failed", blocked)
        return {"sent": False, "reason": blocked, "message_id": message.id}

    try:
        result = sender.send(
            to=to,
            subject=(body.subject or "").strip() or "Liner AI",
            body=body.body or "",
            reply_to=identity.reply_to,
            from_address=identity.from_address,
        )
    except NotConfigured as exc:
        _record("failed", exc.as_dict().get("error", "Not configured."))
        return {"sent": False, "message_id": message.id, **exc.as_dict()}
    _record(
        "sent" if result.status == "sent" else "failed",
        result.detail or "",
        getattr(result, "provider_message_id", "") or "",
    )
    return {
        "message_id": message.id,
        "sent": result.status == "sent",
        "status": result.status,
        "provider": sender.name,
        "from_address": identity.from_address,
        "from_is_personal": identity.personal,
        "from_note": identity.note,
        "reply_to": identity.reply_to,
        "detail": result.detail or "",
    }
