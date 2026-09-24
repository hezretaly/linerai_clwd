"""Reading an email's envelope and files, the same way from every surface.

`email_envelopes` and `email_attachments` hang beside rows that already exist
-- an outbound message beside its `outreach` row, an inbound one beside its
receipt -- and several places need to get from one to the other: the mailbox
list, the buyer's timeline, the reader, the composer that replies to it, and
the send that forwards its files. One module answers "what else did this
message carry", so no two of them disagree about which parent an envelope
hangs off.

**An inbound envelope is found through the receipt.** It hangs off
`inbound_emails` rather than `outreach` because the receipt is the durable
record (a reseed detaches receipts and deletes outreach), so from an inbound
`outreach` row the path is `InboundEmail.outreach_id -> receipt -> envelope`.

**A file is claimed by the send, or copied if it has been sent before.** An
upload sits with no envelope until a message takes it. A rep who forwards a
message, or retries a send that failed, is naming files that already belong to
another envelope; those get a second row pointing at the same bytes (the store
is content-addressed, so nothing is copied on disk) rather than being moved off
the message they arrived on.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import email_addresses, email_files
from app.config import settings
from app.db import utcnow
from app.integrations.email.base import OutgoingAttachment, bare_address
from app.models import EmailAttachment, EmailEnvelope, InboundEmail, Outreach
from app.schemas.serialize import stamp

#: Uploads nobody sent. Cleared by age, lazily, when somebody uploads again.
PENDING_TTL = timedelta(days=2)


class AttachmentError(ValueError):
    """A named attachment cannot go on this message. The text says why."""


# ------------------------------------------------------------------ lookups


def for_outreach(db: Session, row: Outreach | None) -> EmailEnvelope | None:
    """The envelope of one `outreach` row, in either direction."""
    if row is None:
        return None
    return for_outreach_many(db, [row]).get(row.id)


def for_outreach_many(db: Session, rows: Sequence[Outreach]) -> dict[str, EmailEnvelope]:
    """`{outreach_id: envelope}` for every row that has one. Two queries at most."""
    ids = [r.id for r in rows if r is not None and r.channel == "email"]
    if not ids:
        return {}
    found: dict[str, EmailEnvelope] = {
        e.outreach_id: e
        for e in db.scalars(select(EmailEnvelope).where(EmailEnvelope.outreach_id.in_(ids)))
        if e.outreach_id
    }
    inbound = [r.id for r in rows if r is not None and r.direction == "in" and r.id not in found]
    if inbound:
        receipts = {
            rid: oid
            for rid, oid in db.execute(
                select(InboundEmail.id, InboundEmail.outreach_id).where(
                    InboundEmail.outreach_id.in_(inbound)
                )
            )
        }
        if receipts:
            for env in db.scalars(
                select(EmailEnvelope).where(EmailEnvelope.receipt_id.in_(list(receipts)))
            ):
                oid = receipts.get(env.receipt_id or "")
                if oid and oid not in found:
                    found[oid] = env
    return found


def for_receipt(db: Session, receipt_id: str | None) -> EmailEnvelope | None:
    if not receipt_id:
        return None
    return db.scalar(select(EmailEnvelope).where(EmailEnvelope.receipt_id == receipt_id))


def for_receipts_many(db: Session, receipt_ids: Iterable[str]) -> dict[str, EmailEnvelope]:
    ids = [i for i in receipt_ids if i]
    if not ids:
        return {}
    return {
        e.receipt_id: e
        for e in db.scalars(select(EmailEnvelope).where(EmailEnvelope.receipt_id.in_(ids)))
        if e.receipt_id
    }


def receipt_for_outreach(db: Session, outreach_id: str) -> InboundEmail | None:
    """The delivery an inbound `outreach` row was filed from."""
    return db.scalar(select(InboundEmail).where(InboundEmail.outreach_id == outreach_id))


def attachments_of(db: Session, envelope_ids: Iterable[str]) -> dict[str, list[EmailAttachment]]:
    """`{envelope_id: [attachment, ...]}`, in the order they were added."""
    ids = [i for i in envelope_ids if i]
    out: dict[str, list[EmailAttachment]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = db.scalars(
        select(EmailAttachment)
        .where(EmailAttachment.envelope_id.in_(ids))
        .order_by(EmailAttachment.created_at, EmailAttachment.id)
    )
    for a in rows:
        out.setdefault(a.envelope_id or "", []).append(a)
    return out


def by_rfc_message_id(db: Session, message_id: str, *, outbound: bool = False) -> EmailEnvelope | None:
    """The envelope a `Message-ID` names, for threading a reply under it.

    Compared as written and without angle brackets, because one mail client
    quotes `<abc@x>` back and a hand-built header may say `abc@x`.

    `outbound=True` asks only about our own sends. A copy of one of our
    messages can come back in -- a reply-all that includes our own address --
    carrying the very same Message-ID, and the intake deciding whose reply
    this is must find the send, never that copy.
    """
    mid = (message_id or "").strip()
    if not mid:
        return None
    bare = mid.strip("<>")
    query = select(EmailEnvelope).where(EmailEnvelope.rfc_message_id.in_([mid, bare, f"<{bare}>"]))
    if outbound:
        query = query.where(EmailEnvelope.outreach_id.is_not(None))
    return db.scalar(query.order_by(EmailEnvelope.created_at.desc()).limit(1))


# -------------------------------------------------------------- serializers


def attachment_out(a, *, base: str = "/api/email/attachments") -> dict:  # noqa: ANN001
    """One file as the API serves it.

    `url` is the download (an ordinary `<a href>` in the browser, because a
    binary body cannot go through the JSON client) and is empty for a file
    whose bytes were never kept -- a refused type, or a relay that forwarded
    only the name. `inline` says whether the browser may show it in the page:
    a raster image the server has itself recognised, never anything else.

    Takes an `EmailAttachment` or an `OpsMailAttachment`: one shape for both
    realms, so the reader draws a file the same way whichever database it
    came from. Ours has no Content-ID, disposition or refusal -- a refused
    upload never becomes a row -- and reads as a plain attachment.
    """
    refused = getattr(a, "refused", "") or ""
    kept = bool(a.path) and not refused
    return {
        "id": a.id,
        "filename": a.filename,
        "size": a.size or 0,
        "content_type": a.content_type,
        "content_id": getattr(a, "content_id", "") or "",
        "disposition": getattr(a, "disposition", "") or "attachment",
        "refused": refused,
        "inline": kept and a.content_type in email_files.INLINE_TYPES,
        "url": f"{base}/{a.id}" if kept else "",
        "created_at": stamp(a.created_at),
    }


def summary(
    env: EmailEnvelope | None,
    atts: Sequence[EmailAttachment] = (),
    *,
    include_bcc: bool = False,
    base: str = "/api/email/attachments",
) -> dict:
    """What a list row or a timeline card shows beyond the one address.

    Inline images a message refers to from its HTML are left out of
    `attachments`: they are part of the body, and listing a logo from a
    signature as a file the buyer sent is how a rep opens an email expecting
    a document and finds a picture of a car dealership's name.
    """
    files = [a for a in atts if not (a.disposition == "inline" and a.content_id)]
    return {
        "to": email_addresses.as_dicts(env.to_json) if env else [],
        "cc": email_addresses.as_dicts(env.cc_json) if env else [],
        "bcc": email_addresses.as_dicts(env.bcc_json) if (env and include_bcc) else [],
        "reply_to": email_addresses.as_dicts(env.reply_to_json) if env else [],
        "has_html": bool(env and (env.html or "").strip()),
        "importance": (env.importance if env else "normal") or "normal",
        "attachments": [attachment_out(a, base=base) for a in files],
    }


# ------------------------------------------------------------------- ours


def our_addresses() -> set[str]:
    """Every address that is this system rather than a person we write to.

    Used to take ourselves out of a reply-all: answering everybody on a
    message that was sent *to* `sales@` must not Cc `sales@`. Anything at the
    sending domain is ours -- every dealership mailbox, `support@`, `founder@`
    and every `reply+<token>@` live there -- plus the published support and
    founder addresses and whatever `SENDING_FROM` names, which may be on a
    domain of its own.
    """
    from app.integrations.registry import get_email_sender

    found = {
        bare_address(settings.support_email).lower(),
        bare_address(settings.founder_email).lower(),
        bare_address(settings.sending_from).lower(),
    }
    try:
        sender = get_email_sender()
        found.add(bare_address(sender.default_address("dealership")).lower())
        found.add(bare_address(sender.default_address("ops")).lower())
    except Exception:  # noqa: BLE001 -- a misconfigured sender still has the settings above
        pass
    return {a for a in found if a}


def is_our_address(address: str) -> bool:
    bare = bare_address(address).lower() or (address or "").strip().lower()
    if not bare:
        return False
    domain = (settings.sending_domain or "").strip().lower()
    # A dealership's own mail domain is a subdomain of ours
    # (`sales@alsbou.linerai.us`), and it is ours in the sense this asks:
    # a Reply all must not answer it and a round trip may send to it.
    if domain and (bare.endswith("@" + domain) or bare.endswith("." + domain)):
        return True
    return bare in our_addresses()


def without_ours(recipients: Iterable[email_addresses.Recipient]) -> list[email_addresses.Recipient]:
    return [r for r in recipients if not is_our_address(r.address)]


# ------------------------------------------------------- files on a send


def pending_for(db: Session, ids: Sequence[str]) -> list[EmailAttachment]:
    """The rows `ids` name, in the order given. Unknown ids are refused."""
    wanted = [i for i in dict.fromkeys(ids or []) if i]
    if not wanted:
        return []
    rows = {a.id: a for a in db.scalars(select(EmailAttachment).where(EmailAttachment.id.in_(wanted)))}
    missing = [i for i in wanted if i not in rows]
    if missing:
        raise AttachmentError(
            "An attached file is no longer here -- it may have been removed. "
            "Attach it again and send."
        )
    return [rows[i] for i in wanted]


def claim(
    db: Session,
    ids: Sequence[str],
    envelope: EmailEnvelope,
    *,
    user_id: str | None,
) -> list[EmailAttachment]:
    """Put the named files on `envelope`, and return its attachment rows.

    A pending upload is taken only by the person who uploaded it. A file
    already on another message -- forwarding, or retrying a send that failed
    -- is copied: a new row pointing at the same bytes, so the original
    message keeps its files. A refused file (a name with no bytes) cannot be
    sent and is refused here with the reason it was refused before.
    """
    rows = pending_for(db, ids)
    total = 0
    out: list[EmailAttachment] = []
    for a in rows:
        if a.refused or not a.path:
            raise AttachmentError(f"{a.filename} cannot be sent: {a.refused or 'the file was not kept.'}")
        total += a.size or 0
        if a.envelope_id is None:
            if a.uploaded_by and user_id and a.uploaded_by != user_id:
                raise AttachmentError(f"{a.filename} was attached by somebody else.")
            a.envelope_id = envelope.id
            a.uploaded_by = None
            out.append(a)
        elif a.envelope_id == envelope.id:
            out.append(a)
        else:
            copy = EmailAttachment(
                envelope_id=envelope.id,
                filename=a.filename,
                content_type=a.content_type,
                size=a.size,
                sha256=a.sha256,
                path=a.path,
                content_id=a.content_id,
                disposition=a.disposition,
            )
            db.add(copy)
            out.append(copy)
    if total > email_files.MAX_TOTAL:
        raise AttachmentError(
            f"The files add up to {total // (1024 * 1024)} MB; one message can carry "
            f"{email_files.MAX_TOTAL // (1024 * 1024)} MB. Send some of them separately."
        )
    db.flush()
    return out


def check_sendable(db: Session, ids: Sequence[str], *, user_id: str | None) -> list[EmailAttachment]:
    """`claim`'s refusals, without claiming anything: for a send that has
    not made its row yet and must be able to say no first."""
    rows = pending_for(db, ids)
    total = 0
    for a in rows:
        if a.refused or not a.path:
            raise AttachmentError(f"{a.filename} cannot be sent: {a.refused or 'the file was not kept.'}")
        if a.envelope_id is None and a.uploaded_by and user_id and a.uploaded_by != user_id:
            raise AttachmentError(f"{a.filename} was attached by somebody else.")
        if email_files.path_of(a.path) is None:
            raise AttachmentError(f"{a.filename} is missing from disk. Attach it again and send.")
        total += a.size or 0
    if total > email_files.MAX_TOTAL:
        raise AttachmentError(
            f"The files add up to {total // (1024 * 1024)} MB; one message can carry "
            f"{email_files.MAX_TOTAL // (1024 * 1024)} MB. Send some of them separately."
        )
    return rows


def outgoing(rows: Sequence[EmailAttachment]) -> list[OutgoingAttachment]:
    """The files with their bytes, ready for a sender. Refuses a missing one.

    Better to refuse the send than to deliver a message whose text says
    "attached is the credit application" with nothing attached.
    """
    out: list[OutgoingAttachment] = []
    for a in rows:
        data = email_files.read(a.path)
        if data is None:
            raise AttachmentError(f"{a.filename} is missing from disk. Attach it again and send.")
        out.append(
            OutgoingAttachment(
                filename=a.filename,
                content_type=a.content_type or "application/octet-stream",
                data=data,
                content_id=a.content_id if a.disposition == "inline" else "",
            )
        )
    return out


def store_upload(
    db: Session,
    *,
    scope: str,
    filename: str,
    data: bytes,
    declared_type: str,
    user_id: str | None,
) -> EmailAttachment:
    """File one upload from the composer, not yet on any message.

    Refused types and oversize files raise `AttachmentError` -- an upload is
    a person at a keyboard who can pick another file, unlike a received
    attachment, which is kept as a refused row because nobody can ask the
    sender again.
    """
    name = email_files.safe_filename(filename)
    reason = email_files.blocked_reason(name)
    if reason:
        raise AttachmentError(reason)
    if not data:
        raise AttachmentError(f"{name} is empty.")
    if len(data) > email_files.MAX_FILE:
        raise AttachmentError(
            f"{name} is {len(data) // (1024 * 1024)} MB; one file can be at most "
            f"{email_files.MAX_FILE // (1024 * 1024)} MB."
        )
    _clear_stale(db)
    digest, relative = email_files.store(scope, data)
    row = EmailAttachment(
        envelope_id=None,
        uploaded_by=user_id,
        filename=name,
        content_type=email_files.sniff(data, declared_type, name),
        size=len(data),
        sha256=digest,
        path=relative,
    )
    db.add(row)
    db.flush()
    return row


def _clear_stale(db: Session) -> None:
    """Forget uploads nobody sent. The bytes stay: the store is shared."""
    cutoff = utcnow() - PENDING_TTL
    for a in db.scalars(
        select(EmailAttachment).where(
            EmailAttachment.envelope_id.is_(None), EmailAttachment.created_at < cutoff
        )
    ):
        db.delete(a)
