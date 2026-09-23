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

**Our mail is real mail.** A message we write carries every To, Cc and Bcc,
formatted text, files and the headers that thread it under what it answers.
`ops_messages` keeps the one column set it always had -- the first To, the
text half -- and everything else lives beside it in `ops_mail_envelopes` and
`ops_mail_attachments`, because a table that already exists on every
deployment never gains a column.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import email_addresses, email_envelopes, email_files, email_html, outreach_send
from app.config import settings
from app.db import get_ops_db, utcnow
from app.api.deps import require_owner
from app.email_addresses import Recipient
from app.email_envelopes import AttachmentError
from app.events import emit_ops
from app.integrations.base import NotConfigured
from app.integrations.email.base import OutgoingAttachment
from app.integrations.registry import get_email_sender
from app import ops_inbox
from app.email_outbound import display
from app.models import (
    DemoRequest,
    EmailAttachment,
    EmailEnvelope,
    InboundEmail,
    OpsMailAttachment,
    OpsMailEnvelope,
    OpsMailState,
    OpsMessage,
    OpsUser,
)
from app.schemas.serialize import iso, stamp

log = logging.getLogger("liner.ops")

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
        "consented_at": stamp(row.consent_at),
        # The words they agreed to, not just that they agreed. Shown on the
        # entry because that is the only place anyone would ever go looking.
        "consent_text": row.consent_text,
        "status": row.status,
        "unread": row.status == "new",
        "created_at": stamp(row.created_at),
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
    db: Session = Depends(get_ops_db), user: OpsUser = Depends(require_owner)
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
        # Across every store, not just the default -- `inbound_emails` stays
        # on the dealership's side because it keys on leads and outreach.
        "unmatched_mail": ops_inbox.unresolved_count(),
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
    db: Session = Depends(get_ops_db),
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
    db: Session = Depends(get_ops_db),
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
    db: Session = Depends(get_ops_db),
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
        emit_ops("demo.updated", {
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
            "at": stamp(request.created_at),
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
            # A form is not an email: nobody was copied on it and it carries
            # no files. The key is there so every row has one shape.
            "email": _empty_summary(),
        })

    # Plain dicts from every store, because `inbound_emails` lives on the
    # dealership's side of the split and this session is ours.
    for mail in ops_inbox.unresolved(300):
        mark = marks.get(("email", mail["id"]))
        rows.append({
            "id": mail["id"],
            "source": "email",
            "kind": "unmatched",
            "direction": "in",
            # The name off the header From, where the delivery carried one.
            # The envelope sender is a bare address -- a relay does not put a
            # display name in an envelope -- so without this every stranger
            # read as an address.
            "from_name": mail.get("from_name") or "",
            "from_address": mail["from_address"],
            "to_address": mail["to_address"] or "",
            "subject": mail["subject"] or "(no subject)",
            "body": mail["body"] or "",
            "at": stamp(mail["created_at"]),
            # Unread until somebody opens it. This was hardcoded False,
            # because `inbound_emails` has no column for it and there is no
            # Alembic here -- so every delivery arrived looking already read,
            # which is the opposite of what an inbox is for. The mark lives in
            # its own table, which a database that already exists does get.
            "unread": not (mark and mark.read_at),
            "status": mail["outcome"],
            "trashed": bool(mark and mark.trashed_at),
            "slot_at": None,
            "phone": "",
            "dealership": "",
            "dealership_url": "",
            # Built by `ops_inbox` from the receipt's envelope, in the store
            # the delivery landed in. Mail from before envelopes were kept has
            # none, and is filled out to the same shape rather than left
            # without the key.
            "email": _received_summary(mail.get("email")),
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
    messages = (
        db.query(OpsMessage)
        .filter(
            or_(OpsMessage.state != "draft", OpsMessage.author_id == user.id),
        )
        .order_by(OpsMessage.created_at.desc())
        .limit(300)
        .all()
    )
    # Read once for the page rather than once a row: there are two of us, and
    # a query per message to learn which of two names wrote it was 300
    # queries for the same two answers.
    authors = {u.id: u for u in db.query(OpsUser).all()}
    envelopes, files = _mail_of(db, [m.id for m in messages])
    for msg in messages:
        author = authors.get(msg.author_id)
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
            "at": stamp(msg.sent_at or msg.updated_at or msg.created_at),
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
            "email": _summary(msg, envelopes.get(msg.id), files.get(msg.id, [])),
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
    db: Session = Depends(get_ops_db),
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
    """The row a mark is about, or None when the mark needs no row.

    Only `form` returns something the caller writes to -- a demo request
    answers from its own `status`, because the notification bell reads that
    and a second copy is how the two start disagreeing.

    `email` deliberately returns None. An unresolved delivery lives in a
    *dealership's* database, which this session is not, and the read or trash
    mark belongs in `ops_mail_state`, which is ours. So the only thing wanted
    from that side is whether the id exists at all -- a mark stored against
    one that does not is a row pointing at nothing, which the inbox would then
    count.
    """
    if body.kind == "ours":
        row = db.query(OpsMessage).filter_by(id=body.id).one_or_none()
    elif body.kind == "form":
        row = db.query(DemoRequest).filter_by(id=body.id).one_or_none()
    elif body.kind == "email":
        if not ops_inbox.exists(body.id):
            raise HTTPException(404, "No such message")
        return None
    else:
        raise HTTPException(400, "kind must be form, email or ours")
    if row is None:
        raise HTTPException(404, "No such message")
    return row


@router.post("/mail/read")
def mark_read(
    body: ReadBody,
    db: Session = Depends(get_ops_db),
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
    db: Session = Depends(get_ops_db),
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


# ------------------------------------------------------ what a message carries
#
# Every To, Cc and Bcc, the formatted body, the files, and the headers that
# thread a reply under what it answers. `ops_messages` keeps the columns it
# always had -- the first To, bare, and the text half -- because the list, the
# boxes and `make ops-ui`'s clean-up all read them; the rest is the envelope's.

#: What one API field may hold: one string (which may itself carry several
#: addresses, which is what the composer always sent), or a list of strings or
#: of `{name, address}`.
Addresses = Union[str, list[Union[str, dict]], None]

#: Where our own files are downloaded from. The reader and the list both hand
#: this out, so it is one constant rather than two spellings of one path.
ATTACHMENT_URL = "/api/ops/mail/attachments/ours"

#: Outlook reads `Importance`, older clients `X-Priority`. A normal message
#: sends neither, which is what a message with no opinion looks like -- and
#: keeps the provider's request free of a headers block nobody asked for.
IMPORTANCE_HEADERS = {"high": {"Importance": "high", "X-Priority": "1"}}

#: What a row says between being written and the provider answering. Stored,
#: because the row is committed before the send: if the process dies while
#: the request is in flight, this is what the person finds, and it is true.
SENDING = (
    "The send had not finished when this was recorded. If this is still here, "
    "nothing is known to have been delivered."
)

#: What cannot be inside a Message-ID: whitespace, controls and the brackets
#: that delimit one.
_NOT_IN_MSGID = re.compile(r"[\x00-\x20\x7f<>]")


def _empty_summary() -> dict:
    """The email shape for something that is not an email, or knows nothing."""
    return {
        "to": [], "cc": [], "bcc": [], "reply_to": [],
        "has_html": False, "importance": "normal", "attachments": [],
    }


def _received_summary(summary: dict | None) -> dict:
    """What `ops_inbox` read off a delivery's envelope, with every key present.

    Missing keys are filled rather than trusted to exist, so a row from a
    store that has no envelope for it -- older mail, or a store file from
    before envelopes were kept -- is the same shape as every other row.
    """
    out = _empty_summary()
    if isinstance(summary, dict):
        out.update({k: v for k, v in summary.items() if v is not None})
    return out


def _file_out(a: OpsMailAttachment) -> dict:
    """One of our files in the shape `email_envelopes.attachment_out` serves.

    One shape for both realms, so the reader and the list draw a file the same
    way whichever database it came from. Nothing we write is ever refused or
    inline: a refused upload never becomes a row, and the composer has no way
    to put a picture in the body.
    """
    kept = bool(a.path)
    return {
        "id": a.id,
        "filename": a.filename,
        "size": a.size or 0,
        "content_type": a.content_type,
        "content_id": "",
        "disposition": "attachment",
        "refused": "",
        "inline": kept and a.content_type in email_files.INLINE_TYPES,
        "url": f"{ATTACHMENT_URL}/{a.id}" if kept else "",
        "created_at": stamp(a.created_at),
    }


def _summary(
    msg: OpsMessage, env: OpsMailEnvelope | None, files: list[OpsMailAttachment]
) -> dict:
    """What a list row shows about one of our messages beyond its first To.

    Bcc is included: this is our own mail read by the people who wrote it, and
    who was blind-copied is exactly what somebody checks a day later. A
    message from before envelopes were kept still names the one address it
    went to, which is all it ever had.
    """
    if env is not None:
        to = email_addresses.as_dicts(env.to_json)
        cc = email_addresses.as_dicts(env.cc_json)
        bcc = email_addresses.as_dicts(env.bcc_json)
    else:
        to = [{"name": "", "address": msg.to_address}] if msg.to_address else []
        cc, bcc = [], []
    return {
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "reply_to": [{"name": "", "address": msg.reply_to}] if msg.reply_to else [],
        "has_html": bool(env and (env.html or "").strip()),
        "importance": (env.importance if env else "normal") or "normal",
        "attachments": [_file_out(a) for a in files],
    }


def _mail_of(
    db: Session, ids: list[str]
) -> tuple[dict[str, OpsMailEnvelope], dict[str, list[OpsMailAttachment]]]:
    """Envelopes and files for a page of messages, in two queries."""
    if not ids:
        return {}, {}
    envelopes = {
        e.message_id: e
        for e in db.query(OpsMailEnvelope).filter(OpsMailEnvelope.message_id.in_(ids))
    }
    files: dict[str, list[OpsMailAttachment]] = {}
    for a in (
        db.query(OpsMailAttachment)
        .filter(OpsMailAttachment.message_id.in_(ids))
        .order_by(OpsMailAttachment.created_at, OpsMailAttachment.id)
    ):
        files.setdefault(a.message_id or "", []).append(a)
    return envelopes, files


def _envelope(db: Session, message: OpsMessage) -> OpsMailEnvelope:
    """This message's envelope, made on first use. One per message, ever."""
    env = db.query(OpsMailEnvelope).filter_by(message_id=message.id).one_or_none()
    if env is None:
        env = OpsMailEnvelope(message_id=message.id)
        db.add(env)
    return env


def _importance(value: str | None) -> str:
    """normal or high, and nothing else -- a typo is refused, not guessed at."""
    level = (value or "normal").strip().lower()
    if level not in ("normal", "high"):
        raise HTTPException(400, "importance must be normal or high")
    return level


def _recipients(
    to: Addresses, cc: Addresses, bcc: Addresses
) -> tuple[list[Recipient], list[Recipient], list[Recipient]]:
    """Everybody a message about to leave is for, or a 400 naming what is not.

    The refusal quotes the entries it could not read, verbatim: "that does not
    look like an email address" over a line holding six of them sends somebody
    hunting for the one with a typo. Nobody appears twice -- someone in To who
    is also typed into Cc gets one copy -- and there has to be a To.
    """
    parsed = [email_addresses.parse(v) for v in (to, cc, bcc)]
    bad = [entry for _found, entries in parsed for entry in entries]
    if bad:
        named = ", ".join(bad[:5]) + (f" and {len(bad) - 5} more" if len(bad) > 5 else "")
        raise HTTPException(400, f"That does not look like an email address: {named}.")
    to_list, cc_list, bcc_list = email_addresses.distinct(*(found for found, _ in parsed))
    if not to_list:
        raise HTTPException(400, "That does not look like an email address -- the To line is empty.")
    total = len(to_list) + len(cc_list) + len(bcc_list)
    if total > email_addresses.MAX_RECIPIENTS:
        raise HTTPException(
            400,
            f"That is {total} recipients; one message can go to "
            f"{email_addresses.MAX_RECIPIENTS} across To, Cc and Bcc.",
        )
    return to_list, cc_list, bcc_list


def _typed(values: Addresses) -> list[Recipient]:
    """What somebody has typed so far, kept as typed -- for a draft.

    A draft is unfinished by definition, and half an address is part of what
    somebody wrote. So an entry that is not an address yet is kept alongside
    the ones that are, and the composer draws it as the unfinished chip it is
    when the draft is reopened. The refusal waits for the send.
    """
    found, bad = email_addresses.parse(values)
    return found + [Recipient(name="", address=entry) for entry in bad]


def _msgid(value: str | None) -> str:
    """A Message-ID in the angle brackets a header wants, or "".

    A dedupe digest (`sha256:...`) stands in for a missing Message-ID on a
    receipt and names a message that never existed; it is never threaded
    under. Whitespace, control characters and stray brackets are taken out
    rather than trusted: the chain is copied from headers a stranger wrote,
    and it goes back out in a header of ours.
    """
    text = (value or "").strip()
    if not text or text.startswith("sha256:"):
        return ""
    text = _NOT_IN_MSGID.sub("", text)
    return f"<{text}>" if text else ""


def _chain(message_id: str, references: str, in_reply_to: str) -> tuple[str, str]:
    """`(In-Reply-To, References)` for an answer to a message, per RFC 5322.

    References is the parent's own chain, or its In-Reply-To where it had no
    chain, followed by its Message-ID -- which is what lets a client that
    never saw the middle of a conversation still put the reply in it. Where
    the parent's own Message-ID is unknown there is nothing to reply *to*,
    and the chain it did carry is still worth sending. Long chains keep the
    first message and the most recent ones, the trim mail clients themselves
    make.
    """
    parent = _msgid(message_id)
    ids = [_msgid(i) for i in (references or "").split()]
    if not any(ids):
        ids = [_msgid(i) for i in (in_reply_to or "").split()[:1]]
    ids = [i for i in dict.fromkeys(ids + [parent]) if i]
    if len(ids) > 20:
        ids = ids[:1] + ids[-19:]
    return parent, " ".join(ids)


def _receipt_thread(receipt_id: str) -> tuple[str, str, str]:
    """`(Message-ID, References, In-Reply-To)` of a delivery, in whichever
    store it landed.

    Through `ops_inbox`'s walk, which asks `has_database` before it connects:
    a lookup that creates a store file is the bug that walk was written to
    stop. The envelope's Message-ID wins over the receipt's, which is the
    relay's copy and may be the dedupe digest instead of a real one.
    """

    def look(db: Session):
        receipt = db.get(InboundEmail, receipt_id)
        if receipt is None:
            return None
        env = None
        try:
            env = email_envelopes.for_receipt(db, receipt.id)
        except OperationalError:
            # A store file from before envelopes were kept. The receipt's own
            # Message-ID still threads the reply.
            db.rollback()
        message_id = (env.rfc_message_id if env else "") or receipt.message_id or ""
        in_reply_to = (env.in_reply_to if env else "") or receipt.in_reply_to or ""
        return message_id, (env.references if env else "") or "", in_reply_to

    for _slug, found in ops_inbox._each(look):
        if found:
            return found
    return "", "", ""


def _thread(db: Session, kind: str, ref_id: str) -> tuple[str, str]:
    """`(In-Reply-To, References)` for a message answering `kind`/`ref_id`.

    `email` is a delivery nobody could place, answered by its Message-ID.
    `ours` is one of our own sends -- answering a Sent row, or following up on
    one -- threaded under the Message-ID it went out with, **never** the
    provider's API id, which names a request to Resend and would thread the
    reply under nothing at all. A form came off our own website and has no
    Message-ID, so a reply to one starts a thread.
    """
    if not ref_id:
        return "", ""
    if kind == "email":
        return _chain(*_receipt_thread(ref_id))
    if kind == "ours":
        env = db.query(OpsMailEnvelope).filter_by(message_id=ref_id).one_or_none()
        if env is None:
            return "", ""
        return _chain(env.rfc_message_id, env.references, env.in_reply_to)
    return "", ""


# --------------------------------------------------------------- our files


@dataclass(frozen=True)
class _Received:
    """A file that arrived on a delivery nobody could place, being forwarded.

    It lives in a dealership's database, which this session is not; only what
    a copy needs is carried across, as plain values.
    """

    id: str
    filename: str
    content_type: str
    size: int
    sha256: str
    path: str
    refused: str


def _received_files(ids: list[str]) -> dict[str, _Received]:
    """Files on *unplaced* deliveries, by id, from whichever store holds them.

    Only unplaced ones, and that is the realm line: mail a buyer sent a
    dealership is that dealership's, and an id is not a permission. What the
    ops mailbox lists is what it may forward.
    """

    def look(db: Session):
        try:
            rows = (
                db.query(EmailAttachment)
                .join(EmailEnvelope, EmailEnvelope.id == EmailAttachment.envelope_id)
                .join(InboundEmail, InboundEmail.id == EmailEnvelope.receipt_id)
                .filter(EmailAttachment.id.in_(ids), InboundEmail.outcome == "unresolved")
                .all()
            )
        except OperationalError:
            db.rollback()
            return []
        return [
            _Received(a.id, a.filename, a.content_type, a.size or 0, a.sha256, a.path, a.refused or "")
            for a in rows
        ]

    found: dict[str, _Received] = {}
    for _slug, rows in ops_inbox._each(look):
        for row in rows:
            found.setdefault(row.id, row)
    return found


def _named_files(
    db: Session, ids: list[str], user: OpsUser, message_id: str | None
) -> list[OpsMailAttachment | _Received]:
    """The files `ids` names, checked for going on `message_id`, in order.

    `email_envelopes.check_sendable`'s refusals, for our tables: a file that
    is gone, somebody else's upload, somebody else's draft, bytes missing from
    disk, or more than one message can carry. Checked before anything is
    written, so a refusal leaves no row behind.
    """
    wanted = [i for i in dict.fromkeys(ids or []) if i]
    if not wanted:
        return []
    ours = {
        a.id: a
        for a in db.query(OpsMailAttachment).filter(OpsMailAttachment.id.in_(wanted))
    }
    rest = [i for i in wanted if i not in ours]
    theirs = _received_files(rest) if rest else {}
    if any(i not in ours and i not in theirs for i in wanted):
        raise AttachmentError(
            "An attached file is no longer here -- it may have been removed. "
            "Attach it again and send."
        )
    rows: list[OpsMailAttachment | _Received] = [ours.get(i) or theirs[i] for i in wanted]
    total = 0
    for a in rows:
        if isinstance(a, _Received):
            if a.refused or not a.path:
                raise AttachmentError(
                    f"{a.filename} cannot be sent: {a.refused or 'the file was not kept.'}"
                )
        elif a.message_id is None:
            if a.uploaded_by and a.uploaded_by != user.id:
                raise AttachmentError(f"{a.filename} was attached by somebody else.")
        elif a.message_id != message_id:
            # Copying off another message is forwarding or retrying, and fine
            # -- except off a draft that is not ours, which nobody else sees.
            source = db.get(OpsMessage, a.message_id)
            if source is not None and source.state == "draft" and source.author_id != user.id:
                raise AttachmentError(f"{a.filename} is on somebody else's draft.")
        if email_files.path_of(a.path) is None:
            raise AttachmentError(f"{a.filename} is missing from disk. Attach it again and send.")
        total += a.size or 0
    if total > email_files.MAX_TOTAL:
        raise AttachmentError(
            f"The files add up to {total // (1024 * 1024)} MB; one message can carry "
            f"{email_files.MAX_TOTAL // (1024 * 1024)} MB. Send some of them separately."
        )
    return rows


def _attach(
    db: Session, message: OpsMessage, ids: list[str], user: OpsUser
) -> list[OpsMailAttachment]:
    """Put exactly the named files on `message`, and return them in order.

    `email_envelopes.claim` for our own tables. A pending upload is taken by
    the message; a file already on it stays; a file on another message -- a
    forward, or a retry of a send that failed -- is copied, a second row over
    the same bytes, so the message it came from keeps its own. The store is
    content-addressed, so nothing is copied on disk.

    **What is not named leaves.** A draft reopened and saved without one of
    its files has had that file taken off by the person writing it; keeping it
    would send something they removed. The row goes and the bytes stay, since
    another row may share them.
    """
    kept: list[OpsMailAttachment] = []
    for a in _named_files(db, ids, user, message.id):
        if isinstance(a, OpsMailAttachment) and a.message_id is None:
            a.message_id = message.id
            a.uploaded_by = None
            kept.append(a)
        elif isinstance(a, OpsMailAttachment) and a.message_id == message.id:
            kept.append(a)
        else:
            copy = OpsMailAttachment(
                message_id=message.id,
                filename=a.filename,
                content_type=a.content_type,
                size=a.size,
                sha256=a.sha256,
                path=a.path,
            )
            db.add(copy)
            kept.append(copy)
    for old in db.query(OpsMailAttachment).filter(OpsMailAttachment.message_id == message.id):
        if not any(old is k for k in kept):
            db.delete(old)
    db.flush()
    return kept


def _outgoing(files: list[OpsMailAttachment]) -> list[OutgoingAttachment]:
    """The files with their bytes, for the sender. Refuses one that vanished.

    Better a failed send than a message whose text says "attached" with
    nothing attached.
    """
    out: list[OutgoingAttachment] = []
    for a in files:
        data = email_files.read(a.path)
        if data is None:
            raise AttachmentError(f"{a.filename} is missing from disk. Attach it again and send.")
        out.append(
            OutgoingAttachment(
                filename=a.filename,
                content_type=a.content_type or "application/octet-stream",
                data=data,
            )
        )
    return out


def _content(html: str | None, body: str | None) -> tuple[str, str]:
    """`(html, text)` of what was written.

    The HTML is cleaned to what the editor can produce, and the text half is
    written *from* it, so the two cannot say different things. With no HTML
    the text is the message and the sender builds its own HTML from it.
    """
    cleaned = email_html.clean_outbound(html) if (html or "").strip() else ""
    text = email_html.text_from_html(cleaned) if cleaned else (body or "")
    return cleaned, text


class DraftBody(BaseModel):
    #: Present when updating one that already exists.
    id: str | None = None
    to: Addresses = ""
    cc: Addresses = None
    bcc: Addresses = None
    subject: str = ""
    body: str = ""
    #: The formatted body. When present the text half is written from it.
    html: str | None = ""
    #: Uploads to keep on the draft, and files already on it to keep there.
    attachment_ids: list[str] | None = None
    importance: str | None = "normal"
    reply_to_kind: str = ""
    reply_to_id: str = ""


@router.post("/mail/draft")
def save_draft(
    body: DraftBody,
    db: Session = Depends(get_ops_db),
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

    Its Cc, Bcc, formatting and files are kept with it, the way its words are
    -- a draft that came back without the attachment somebody spent a minute
    finding is a draft that has to be written twice.
    """
    to, cc, bcc = email_addresses.distinct(_typed(body.to), _typed(body.cc), _typed(body.bcc))
    html, text = _content(body.html, body.body)
    ids = [i for i in (body.attachment_ids or []) if i]
    if not (to or cc or bcc or body.subject.strip() or text.strip() or ids):
        raise HTTPException(400, "Nothing to save yet.")
    importance = _importance(body.importance)
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
    draft.to_address = to[0].address if to else ""
    draft.subject = body.subject.strip()
    draft.body = text
    # Kept when the save does not say: a draft reopened from Drafts does not
    # know what it was answering, and saving it again used to erase that --
    # so the reply went out as a new thread in their inbox.
    draft.reply_to_kind = body.reply_to_kind or draft.reply_to_kind or ""
    draft.reply_to_id = body.reply_to_id or draft.reply_to_id or ""
    draft.updated_at = utcnow()
    db.flush()

    env = _envelope(db, draft)
    env.to_json = email_addresses.dumps(to)
    env.cc_json = email_addresses.dumps(cc)
    env.bcc_json = email_addresses.dumps(bcc)
    env.html = html
    env.importance = importance
    env.in_reply_to, env.references = _thread(db, draft.reply_to_kind, draft.reply_to_id)
    try:
        files = _attach(db, draft, ids, user)
    except AttachmentError as exc:
        # Nothing written: a draft that saved without the file it was asked
        # to keep would say "kept" about something it did not keep.
        db.rollback()
        raise HTTPException(400, str(exc)) from None
    db.commit()
    return {
        "id": draft.id,
        "state": draft.state,
        "updated_at": stamp(draft.updated_at),
        "email": _summary(draft, env, files),
    }


class ReplyBody(BaseModel):
    to: Addresses = ""
    cc: Addresses = None
    bcc: Addresses = None
    subject: str = ""
    body: str = ""
    #: The formatted body. When present the text half is written from it.
    html: str | None = ""
    #: New uploads, files already on the draft, and files on another message
    #: being forwarded -- ours, or on a delivery nobody could place.
    attachment_ids: list[str] | None = None
    importance: str | None = "normal"
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
    db: Session = Depends(get_ops_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """Write from the ops inbox -- an answer, or a first message.

    Both, through one handler. Reaching a dealership we want to talk to is the
    same act as answering one that wrote in, and a second endpoint for it is
    how one of the two stops going through `blocked_reason`.

    Through the same sender and the same outbound limit as everything else --
    `OUTBOUND_ONLY_TO` is exactly as load-bearing here as it is on a dealer's
    composer, and a reply typed to a real prospect from a rehearsal is the
    failure it exists to stop. **Every** recipient is checked, Cc and Bcc
    included: a limit that read only the To line would let a rehearsal copy a
    real prospect in.

    Under the sender's own name where the deployment can prove it owns the
    address, and back to them either way. Two people share this inbox: a reply
    that always came from `support@` read like a ticket, and one that always
    came back to the founder sent half the answers to the wrong person.

    **The row is written before the wire is touched, and whatever the sender
    does is written onto it.** It used to be committed only after the send
    returned, and only `NotConfigured` was caught -- so a provider that raised
    anything else left no row at all, and a person who pressed Send had no
    Sent item and no error to find the next morning.
    """
    to, cc, bcc = _recipients(body.to, body.cc, body.bcc)
    importance = _importance(body.importance)
    html, text = _content(body.html, body.body)
    subject = (body.subject or "").strip()
    ids = [i for i in (body.attachment_ids or []) if i]
    if not (subject or text.strip() or ids):
        raise HTTPException(400, "Nothing to send yet -- write a subject or a message.")
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
        if message is not None and message.state == "sent":
            # Sending it again in place would overwrite the Sent item with a
            # second message under the first one's date. A second message is
            # a new row; a failed one may be sent again where it stands.
            raise HTTPException(409, "That message has already been sent.")
    if message is None:
        message = OpsMessage(author_id=user.id)
        db.add(message)
    message.to_address = to[0].address
    message.subject = subject
    message.body = text
    message.reply_to_kind = body.reply_to_kind or message.reply_to_kind
    message.reply_to_id = body.reply_to_id or message.reply_to_id
    message.from_address = identity.from_address
    message.reply_to = identity.reply_to
    message.provider = sender.name
    message.state = "failed"
    message.detail = SENDING
    message.provider_message_id = ""
    message.sent_at = None
    message.updated_at = utcnow()
    db.flush()

    env = _envelope(db, message)
    env.to_json = email_addresses.dumps(to)
    env.cc_json = email_addresses.dumps(cc)
    env.bcc_json = email_addresses.dumps(bcc)
    env.html = html
    env.importance = importance
    env.rfc_message_id = ""
    # Decided now rather than when the draft was saved: the message it
    # answers may have had its Message-ID reported since.
    env.in_reply_to, env.references = _thread(db, message.reply_to_kind, message.reply_to_id)
    try:
        files = _attach(db, message, ids, user)
    except AttachmentError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from None

    def _record(state: str, detail: str, result=None) -> None:
        """What happened, kept whatever it was.

        A refused send stays as `failed` rather than being discarded: it is
        the one a person most needs to find again, and dropping the row loses
        what they typed along with it.
        """
        message.state = state
        message.detail = detail
        # The provider's own id for the request. It was read as
        # `provider_message_id`, an attribute `SendResult` has never had, so
        # every row stored "" and nothing could be looked up at the provider.
        message.provider_message_id = (result.message_id or "") if result else ""
        message.updated_at = utcnow()
        message.sent_at = utcnow() if state == "sent" else None
        # The Message-ID it really went out with, which is what a reply to it
        # threads under. Only ever what the sender reported; "" otherwise.
        env.rfc_message_id = (result.rfc_message_id or "") if result else ""
        db.commit()

    def _out(sent: bool, status: str, detail: str, **extra) -> dict:
        return {
            "message_id": message.id,
            "sent": sent,
            "status": status,
            "provider": sender.name,
            "from_address": identity.from_address,
            "from_is_personal": identity.personal,
            "from_note": identity.note,
            "reply_to": identity.reply_to,
            "detail": detail,
            **extra,
            "email": _summary(message, env, files),
        }

    blocked = outreach_send.blocked_reason(sender, [r.address for r in to + cc + bcc])
    if blocked:
        _record("failed", blocked)
        return _out(False, "failed", blocked, reason=blocked)

    db.commit()
    try:
        result = sender.send(
            to=[display(r) for r in to],
            subject=subject or "Liner AI",
            body=text,
            reply_to=identity.reply_to,
            in_reply_to=env.in_reply_to,
            from_address=identity.from_address,
            cc=[display(r) for r in cc] or None,
            bcc=[display(r) for r in bcc] or None,
            html=html,
            attachments=_outgoing(files) or None,
            references=env.references,
            headers=IMPORTANCE_HEADERS.get(importance),
            # Per attempt, not per row: a failed message sent again where it
            # stands is a different request, and reusing the key would have
            # the provider refuse it as a replay. A retry *inside* this call
            # carries the same key, which is what the key is for.
            idempotency_key=f"ops-{message.id}/{message.updated_at:%Y%m%d%H%M%S%f}",
        )
    except NotConfigured as exc:
        _record("failed", str(exc))
        return _out(False, "failed", exc.detail or str(exc), **{
            k: v for k, v in exc.as_dict().items() if k != "detail"
        })
    except AttachmentError as exc:
        _record("failed", str(exc))
        return _out(False, "failed", str(exc))
    except Exception as exc:  # noqa: BLE001 -- whatever broke, the row says so
        log.exception("ops send %s raised", message.id)
        detail = f"The send did not go through: {exc}"
        _record("failed", detail)
        return _out(False, "failed", detail)
    _record(
        "sent" if result.status == "sent" else "failed",
        result.detail or "",
        result,
    )
    return _out(result.status == "sent", result.status, result.detail or "")
