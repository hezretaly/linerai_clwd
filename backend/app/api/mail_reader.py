"""Reading mail and handling its files: the reader, uploads and downloads.

Both realms live here -- a dealership's mail under `/api/email/...` and
Liner's own under `/api/ops/mail/...` -- because what they share is the hard
part: HTML that must be cleaned before a browser draws it, `cid:` images that
must become `data:` URIs, and files that must never be served from our own
origin as anything a browser would render.

**One shape for a message, whichever table it came from.** A placed email is
an `outreach` row, a stranger's is only a receipt, ours is an `ops_messages`
row and a form is a demo request; the reader answers all of them as the same
`MailContent`, so the page that draws one draws them all and cannot start
disagreeing with itself about what a Cc is.

**The reply is worked out here, not in the browser.** Who a reply goes to is
a rule -- Reply-To over From, our own addresses taken out, nobody Bcc'd ever
carried forward -- and a rule written once on the server is one the dealer's
page and the ops page cannot apply two different ways.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Sequence
from urllib.parse import unquote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import email_addresses, email_envelopes, email_files, email_html, ops_inbox
from app.api.deps import current_user, require_owner
from app.db import active_store, get_db, get_ops_db, utcnow
from app.email_addresses import Recipient
from app.email_envelopes import AttachmentError
from app.email_intake import REPLY_RE, is_ours
from app.integrations.email.base import bare_address
from app.models import (
    EmailAttachment,
    EmailEnvelope,
    InboundEmail,
    Outreach,
    User,
)
from app.models.ops import DemoRequest, OpsMailAttachment, OpsMailEnvelope, OpsMessage, OpsUser
from app.schemas.serialize import stamp

router = APIRouter(tags=["mail-reader"])

#: Where each realm's files download from. `url` on every `Attachment` is
#: built from one of these, and the frontend resolves it as given.
DEALER_FILES = "/api/email/attachments"
OPS_OURS = "/api/ops/mail/attachments/ours"
OPS_RECEIVED = ops_inbox.ATTACHMENT_URL

#: How much inline imagery one reader response may carry as `data:` URIs.
#: Each is capped at 3 MB by `email_files.data_uri`; a newsletter with twenty
#: of them would otherwise be a sixty-megabyte JSON body for one message. What
#: does not fit is listed as a file instead of drawn.
INLINE_BUDGET = 8 * 1024 * 1024

_CID_REF = re.compile(r"""cid:([^"'\s>)]+)""", re.IGNORECASE)


# ------------------------------------------------------------------ shaping


def _one(text: str | None) -> Recipient | None:
    """A stored `Name <address>` (or bare address) as a recipient."""
    found = email_addresses.from_header_value(text or "")
    if found:
        return found[0]
    from email.utils import parseaddr

    name, address = parseaddr(text or "")
    return Recipient(name=name.strip(), address=address.strip()) if "@" in address else None


def _addr(r: Recipient | None) -> dict:
    return r.as_dict() if r is not None else {"name": "", "address": ""}


def _typed(r: Recipient) -> str:
    """One recipient as the composer's box shows it, ready to send back.

    `Name <address>`, unencoded, because a person reads it before it goes
    anywhere. A name carrying a comma, a semicolon or a quote is dropped for
    the bare address: the box splits on those, and a reply that reaches the
    right person without their name beats one addressed to half of it.
    """
    name = re.sub(r"[\x00-\x1f\x7f]", " ", r.name or "").strip()
    if not name or re.search(r'[,;"<>@\\]', name) or name.lower() == r.address.lower():
        return r.address
    return f"{name} <{r.address}>"


def _prefixed(prefix: str, subject: str, *also: str) -> str:
    """`Re: ` or `Fwd: ` once, whatever case the thread already used it in."""
    text = (subject or "").strip()
    lowered = text.lower()
    if any(lowered.startswith(p) for p in (prefix.lower(), *also)):
        return text
    return f"{prefix} {text}".strip()


def _is_ours(r: Recipient, also: set[str]) -> bool:
    """`email_envelopes.is_our_address`, plus the two things that are ours
    whatever the settings say: the address this very message was delivered to
    (it reached us, so it is ours -- which matters on a deployment with no
    `SENDING_DOMAIN` yet), and any `reply+<token>@`, which is minted by a send
    and routes back into a timeline."""
    return (
        email_envelopes.is_our_address(r.address)
        or r.key in also
        or bool(REPLY_RE.search(r.address))
    )


def _without(people: Iterable[Recipient], taken: set[str], also: set[str]) -> list[Recipient]:
    out = []
    for r in people:
        if r.key in taken or _is_ours(r, also):
            continue
        taken.add(r.key)
        out.append(r)
    return out


def suggestions(
    *,
    outbound: bool,
    sender: Recipient | None,
    to: Sequence[Recipient],
    cc: Sequence[Recipient],
    reply_to: Sequence[Recipient],
    subject: str,
    forward_ids: Sequence[str],
    also_ours: Iterable[str] = (),
) -> dict:
    """Who Reply, Reply all and Forward start out addressed to.

    Reply goes where the sender asked replies to go -- `Reply-To`, which is
    how a marketplace lead arriving from `noreply@` still reaches the buyer
    behind it -- and to the sender otherwise. Answering one of our own
    messages means writing to the people it went to, not to ourselves, which
    is what the ops page used to do. Reply all keeps everyone else who was
    visibly on it in Cc. Our own addresses are never put back on a reply: a
    message sent to `sales@` answered with `sales@` in Cc mails the
    dealership a copy of itself. Nobody Bcc'd is carried forward, ever.
    """
    also = {a.strip().lower() for a in also_ours if a and a.strip()}
    if outbound:
        target = _without(to, set(), also)
    else:
        target = _without(reply_to, set(), also) or _without([sender] if sender else [], set(), also)
    taken = {r.key for r in target}
    everyone = _without([*to, *cc], set(taken), also)
    reply = {"to": [_typed(r) for r in target], "cc": [], "subject": _prefixed("Re:", subject)}
    return {
        "reply": reply,
        "reply_all": {
            "to": reply["to"],
            "cc": [_typed(r) for r in everyone if r.key not in taken],
            "subject": reply["subject"],
        },
        "forward": {
            "subject": _prefixed("Fwd:", subject, "fw:"),
            "attachment_ids": list(forward_ids),
        },
    }


def _referenced(html: str) -> set[str]:
    return {unquote(m).strip("<>").lower() for m in _CID_REF.findall(html or "")}


def _split_files(files: Sequence, html: str) -> tuple[list, dict[str, str]]:
    """`(listed, inline)`: the files to show under the message, and the
    pictures to draw inside it as `data:` URIs keyed by Content-ID.

    A picture goes inside only when the HTML refers to it and it can be drawn
    safely -- a raster image, small enough. Anything else is listed, so a
    Content-ID image too big to inline is a file a rep can open rather than a
    hole in the message and nothing under it.
    """
    wanted = _referenced(html)
    inline: dict[str, str] = {}
    listed = []
    budget = INLINE_BUDGET
    for a in files:
        cid = (getattr(a, "content_id", "") or "").strip()
        refused = getattr(a, "refused", "") or ""
        if cid and cid.lower() in wanted and a.path and not refused:
            data = email_files.read(a.path)
            uri = email_files.data_uri(data, a.content_type) if data else None
            if uri and len(uri) <= budget:
                inline[cid] = uri
                budget -= len(uri)
                continue
        listed.append(a)
    return listed, inline


def _content(
    *,
    kind: str,
    item_id: str,
    direction: str,
    subject: str,
    sender: Recipient | None,
    to: Sequence[Recipient],
    cc: Sequence[Recipient] = (),
    bcc: Sequence[Recipient] = (),
    reply_to: Sequence[Recipient] = (),
    date: datetime | None,
    text: str,
    html: str = "",
    importance: str = "normal",
    message_id: str = "",
    files: Sequence = (),
    base: str = DEALER_FILES,
    lead_id: str | None = None,
    images: bool = False,
    also_ours: Iterable[str] = (),
) -> dict:
    """One message as `MailContent`.

    The HTML is cleaned here, every time it is read, never when it was filed:
    a stricter allowlist tomorrow applies to mail that arrived today. Our own
    sent HTML is already clean and goes through the same cleaner anyway --
    the reader does not get to trust a column because of what usually writes
    it.
    """
    listed, inline = _split_files(files, html)
    cleaned, held = ("", 0)
    if (html or "").strip():
        cleaned, held = email_html.clean_inbound(html, inline=inline, images=images)
    attachments = [email_envelopes.attachment_out(a, base=base) for a in listed]
    return {
        "kind": kind,
        "id": item_id,
        "direction": direction,
        "subject": subject or "",
        "from": _addr(sender),
        "to": [r.as_dict() for r in to],
        "cc": [r.as_dict() for r in cc],
        "bcc": [r.as_dict() for r in bcc],
        "reply_to": [r.as_dict() for r in reply_to],
        "date": stamp(date),
        "text": text or "",
        "html": cleaned,
        "images_held": held,
        "importance": importance if importance in ("normal", "high") else "normal",
        "message_id": "" if (message_id or "").startswith("sha256:") else (message_id or ""),
        "attachments": attachments,
        "lead_id": lead_id,
        **suggestions(
            outbound=direction == "out",
            sender=sender,
            to=to,
            cc=cc,
            reply_to=reply_to,
            subject=subject,
            forward_ids=[a["id"] for a in attachments if a["url"]],
            also_ours=also_ours,
        ),
    }


def _people(text: str | None) -> list[Recipient]:
    return email_addresses.loads(text)


# ------------------------------------------------------- a dealership's mail


def _envelope_sender(env: EmailEnvelope | None, fallback: str) -> Recipient | None:
    if env is not None and env.from_address:
        return Recipient(name=env.from_name or "", address=env.from_address)
    return _one(fallback)


def _dealership_address(db: Session) -> Recipient | None:
    """Who the dealership's own mail is from, for a row too old to say."""
    try:
        from app import outreach_send
        from app.integrations.registry import get_email_sender

        return _one(outreach_send.dealership_from(db, get_email_sender()))
    except Exception:  # noqa: BLE001 -- a misconfigured sender still has a message to read
        return None


def _received(
    db: Session,
    *,
    kind: str,
    item_id: str,
    receipt: InboundEmail | None,
    env: EmailEnvelope | None,
    row: Outreach | None,
    base: str,
    images: bool,
) -> dict:
    """A message somebody sent us, placed or not."""
    files = email_envelopes.attachments_of(db, [env.id]).get(env.id, []) if env else []
    sender = _envelope_sender(env, (receipt.from_address if receipt else "") or (row.to_address if row else ""))
    to = _people(env.to_json) if env else []
    if not to:
        # Mail from before the header lists were kept: the one address the
        # mail server delivered it to is still true, and says whose it was.
        delivered = _one(receipt.to_address) if receipt and receipt.to_address else None
        fallback = delivered or _dealership_address(db)
        to = [fallback] if fallback else []
    arrived = (receipt.created_at if receipt else None) or (row.sent_at if row else None)
    return _content(
        kind=kind,
        item_id=item_id,
        direction="in",
        subject=(receipt.subject if receipt else "") or (row.subject if row else ""),
        sender=sender,
        to=to,
        cc=_people(env.cc_json) if env else [],
        reply_to=_people(env.reply_to_json) if env else [],
        date=(env.dated_at if env else None) or arrived or (row.created_at if row else None),
        # The whole of what arrived, quoted thread included. The list and the
        # timeline show the trimmed reply; the reader is where the rest is.
        text=(receipt.body if receipt and receipt.body else (row.body if row else "")),
        html=env.html if env else "",
        importance=env.importance if env else "normal",
        message_id=(env.rfc_message_id if env else "")
        or (receipt.message_id if receipt else "")
        or ((row.provider_message_id or "") if row else ""),
        files=files,
        base=base,
        lead_id=(row.lead_id if row else None) or (receipt.lead_id if receipt else None),
        images=images,
        also_ours=[bare_address(receipt.to_address)] if receipt and receipt.to_address else [],
    )


@router.get("/email/read/{kind}/{item_id}")
def read_dealer(
    kind: str,
    item_id: str,
    images: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """One of this dealership's emails, whole, ready to draw.

    `message` is an `outreach` row in either direction; `unmatched` is a
    delivery nobody could place, which exists only as its receipt. A text
    message is not an email and is a 404 here -- the reader has nothing to
    say about one that the timeline does not.
    """
    if kind == "message":
        row = db.get(Outreach, item_id)
        if row is None or row.channel != "email":
            raise HTTPException(404, "No such email.")
        env = email_envelopes.for_outreach(db, row)
        if row.direction == "in":
            receipt = email_envelopes.receipt_for_outreach(db, row.id)
            return _received(db, kind=kind, item_id=row.id, receipt=receipt, env=env,
                             row=row, base=DEALER_FILES, images=bool(images))
        files = email_envelopes.attachments_of(db, [env.id]).get(env.id, []) if env else []
        sender = _envelope_sender(env, "") or _dealership_address(db)
        to = _people(env.to_json) if env else []
        if not to and row.to_address:
            one = _one(row.to_address)
            to = [one] if one else []
        return _content(
            kind=kind,
            item_id=row.id,
            direction="out",
            subject=row.subject,
            sender=sender,
            to=to,
            cc=_people(env.cc_json) if env else [],
            # Staff reading the dealership's own sent mail: who was
            # blind-copied is exactly what somebody checks a day later.
            bcc=_people(env.bcc_json) if env else [],
            reply_to=_people(env.reply_to_json) if env else [],
            date=row.sent_at or row.created_at,
            text=row.body,
            html=env.html if env else "",
            importance=env.importance if env else "normal",
            message_id=env.rfc_message_id if env else "",
            files=files,
            base=DEALER_FILES,
            lead_id=row.lead_id,
            images=bool(images),
        )

    if kind == "unmatched":
        receipt = db.get(InboundEmail, item_id)
        # Only mail that is really there to read -- not a refusal, not a
        # duplicate -- and **never mail addressed to Liner**: `support@`,
        # `founder@` and `cto@` are ours, `/ops` lists them, and the mailbox
        # leaves them out for the same reason. An id is not a permission.
        if (
            receipt is None
            or receipt.outcome not in ("unresolved", "accepted")
            or (receipt.outreach_id is None and is_ours(receipt.to_address))
        ):
            raise HTTPException(404, "No such email.")
        env = email_envelopes.for_receipt(db, receipt.id)
        row = db.get(Outreach, receipt.outreach_id) if receipt.outreach_id else None
        return _received(db, kind=kind, item_id=receipt.id, receipt=receipt, env=env,
                         row=row, base=DEALER_FILES, images=bool(images))

    raise HTTPException(404, "Unknown kind of message.")


async def _read_upload(file: UploadFile) -> bytes:
    """The upload's bytes, refusing one over the size limit with a 413
    before holding more of it than the limit."""
    data = await file.read(email_files.MAX_FILE + 1)
    if len(data) > email_files.MAX_FILE:
        raise HTTPException(
            413,
            f"{email_files.safe_filename(file.filename)} is over "
            f"{email_files.MAX_FILE // (1024 * 1024)} MB, the most one file can be.",
        )
    return data


@router.post("/email/attachments")
async def upload_dealer(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """A file for a message being written. It belongs to nothing until a send
    takes it, and to nobody but the person who uploaded it until then."""
    data = await _read_upload(file)
    try:
        row = email_envelopes.store_upload(
            db,
            scope=email_files.scope_for(active_store()),
            filename=file.filename or "",
            data=data,
            declared_type=file.content_type or "",
            user_id=user.id,
        )
    except AttachmentError as exc:
        raise HTTPException(400, str(exc)) from None
    db.commit()
    return email_envelopes.attachment_out(row, base=DEALER_FILES)


@router.delete("/email/attachments/{attachment_id}")
def remove_dealer(
    attachment_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Take a file off a message not yet sent. Only a pending upload, and only
    one's own: a file on a sent message is a record of what was sent."""
    row = db.get(EmailAttachment, attachment_id)
    if row is None or row.envelope_id is not None or row.uploaded_by != user.id:
        raise HTTPException(404, "No such pending file.")
    db.delete(row)
    db.commit()
    return {"ok": True, "id": attachment_id}


def _serve(filename: str, content_type: str, relative: str, refused: str, inline: bool) -> FileResponse:
    """The stored bytes, with the headers that keep them inert.

    A download, never a page: `served_type` gives anything but a raster image
    or a PDF `application/octet-stream`, `nosniff` holds the browser to it,
    and the sandbox CSP gives a file that somehow renders an opaque origin
    and no script. A refused file has no bytes to serve, and one missing from
    disk is a 404 rather than an empty download that looks like a file.
    """
    path = email_files.path_of(relative) if not refused else None
    if path is None:
        raise HTTPException(404, "That file is not available.")
    return FileResponse(
        path,
        media_type=email_files.served_type(content_type, inline=inline),
        headers=email_files.download_headers(filename, content_type, inline=inline),
    )


@router.get("/email/attachments/{attachment_id}")
def download_dealer(
    attachment_id: str,
    inline: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FileResponse:
    row = db.get(EmailAttachment, attachment_id)
    if row is None:
        raise HTTPException(404, "No such file.")
    if row.envelope_id is None:
        # Somebody's upload for a message they have not sent yet.
        if row.uploaded_by != user.id:
            raise HTTPException(404, "No such file.")
    else:
        env = db.get(EmailEnvelope, row.envelope_id)
        receipt = db.get(InboundEmail, env.receipt_id) if env and env.receipt_id else None
        # The same realm line the reader draws: a file on unplaced mail to
        # Liner's own addresses is ours, not the dealership's to open.
        if receipt is not None and receipt.outreach_id is None and is_ours(receipt.to_address):
            raise HTTPException(404, "No such file.")
    return _serve(row.filename, row.content_type, row.path, row.refused, bool(inline))


# ------------------------------------------------------------- Liner's mail


def _form_subject(request: DemoRequest) -> str:
    # The words the ops list uses for the same row, so the reader's heading
    # and the list line above it say the same thing.
    if request.kind == "demo":
        return f"Demo request -- {request.dealership or request.name}"
    return f"Support -- {request.name}"


def _form_text(request: DemoRequest) -> str:
    if request.message:
        return request.message
    from app.api.ops import _demo_body

    return _demo_body(request)


def _ours_sender(msg: OpsMessage, user: OpsUser) -> Recipient | None:
    found = _one(msg.from_address)
    if found is not None:
        return found
    # A draft has not been sent from anywhere yet; it will go from the
    # person writing it.
    author = user if msg.author_id == user.id else None
    return Recipient(name=author.name, address=author.email) if author else None


@router.get("/ops/mail/read/{kind}/{item_id}")
def read_ops(
    kind: str,
    item_id: str,
    images: int = 0,
    db: Session = Depends(get_ops_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """One message in Liner's own mailbox, whole, ready to draw.

    `form` came off our own website and is text; `email` is a delivery nobody
    could place, read in whichever store it landed in; `ours` is something we
    wrote -- a draft (its author's only) or a sent message.
    """
    if kind == "form":
        request = db.get(DemoRequest, item_id)
        if request is None:
            raise HTTPException(404, "No such message.")
        sender = Recipient(name=request.name or "", address=request.email) if request.email else None
        return _content(
            kind=kind, item_id=request.id, direction="in", subject=_form_subject(request),
            sender=sender, to=[], date=request.created_at, text=_form_text(request),
            base=OPS_OURS, images=bool(images),
        )

    if kind == "email":
        def look(store_db: Session) -> dict | None:
            receipt = store_db.get(InboundEmail, item_id)
            # Unplaced only, which is the realm line: mail a buyer sent a
            # dealership is that dealership's, and the ops list shows only
            # what nobody could place.
            if receipt is None or receipt.outcome != "unresolved":
                return None
            try:
                env = email_envelopes.for_receipt(store_db, receipt.id)
            except OperationalError:
                # A store file from before envelopes were kept, on a box that
                # has not restarted since. The receipt still reads.
                store_db.rollback()
                env = None
            return _received(store_db, kind=kind, item_id=receipt.id, receipt=receipt, env=env,
                             row=None, base=OPS_RECEIVED, images=bool(images))

        for _slug, found in ops_inbox._each(look):
            if found is not None:
                found["lead_id"] = None
                return found
        raise HTTPException(404, "No such message.")

    if kind == "ours":
        msg = db.get(OpsMessage, item_id)
        if msg is None or (msg.state == "draft" and msg.author_id != user.id):
            raise HTTPException(404, "No such message.")
        env = db.query(OpsMailEnvelope).filter_by(message_id=msg.id).one_or_none()
        files = (
            db.query(OpsMailAttachment)
            .filter(OpsMailAttachment.message_id == msg.id)
            .order_by(OpsMailAttachment.created_at, OpsMailAttachment.id)
            .all()
        )
        to = _people(env.to_json) if env else []
        if not to and msg.to_address:
            one = _one(msg.to_address)
            to = [one] if one else []
        reply_to = [r for r in [_one(msg.reply_to)] if r] if msg.reply_to else []
        return _content(
            kind=kind,
            item_id=msg.id,
            direction="out",
            subject=msg.subject,
            sender=_ours_sender(msg, user),
            to=to,
            cc=_people(env.cc_json) if env else [],
            bcc=_people(env.bcc_json) if env else [],
            reply_to=reply_to,
            date=msg.sent_at or msg.updated_at or msg.created_at,
            text=msg.body,
            html=env.html if env else "",
            importance=env.importance if env else "normal",
            message_id=env.rfc_message_id if env else "",
            files=files,
            base=OPS_OURS,
            images=bool(images),
        )

    raise HTTPException(404, "Unknown kind of message.")


def _clear_stale_ops(db: Session) -> None:
    """Forget our uploads nobody sent, as `email_envelopes` does for a
    dealership's. The bytes stay: the store is content-addressed and shared."""
    cutoff = utcnow() - email_envelopes.PENDING_TTL
    for a in db.query(OpsMailAttachment).filter(
        OpsMailAttachment.message_id.is_(None), OpsMailAttachment.created_at < cutoff
    ):
        db.delete(a)


@router.post("/ops/mail/attachments")
async def upload_ops(
    file: UploadFile = File(...),
    db: Session = Depends(get_ops_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    """A file for a message of ours being written. Bytes under
    `var/attachments/ops/`, the row in our own database."""
    data = await _read_upload(file)
    name = email_files.safe_filename(file.filename)
    reason = email_files.blocked_reason(name)
    if reason:
        raise HTTPException(400, reason)
    if not data:
        raise HTTPException(400, f"{name} is empty.")
    _clear_stale_ops(db)
    digest, relative = email_files.store("ops", data)
    row = OpsMailAttachment(
        message_id=None,
        uploaded_by=user.id,
        filename=name,
        content_type=email_files.sniff(data, file.content_type or "", name),
        size=len(data),
        sha256=digest,
        path=relative,
    )
    db.add(row)
    db.commit()
    return email_envelopes.attachment_out(row, base=OPS_OURS)


@router.delete("/ops/mail/attachments/{attachment_id}")
def remove_ops(
    attachment_id: str,
    db: Session = Depends(get_ops_db),
    user: OpsUser = Depends(require_owner),
) -> dict:
    row = db.get(OpsMailAttachment, attachment_id)
    if row is None or row.message_id is not None or row.uploaded_by != user.id:
        raise HTTPException(404, "No such pending file.")
    db.delete(row)
    db.commit()
    return {"ok": True, "id": attachment_id}


@router.get("/ops/mail/attachments/{source}/{attachment_id}")
def download_ops(
    source: str,
    attachment_id: str,
    inline: int = 0,
    db: Session = Depends(get_ops_db),
    user: OpsUser = Depends(require_owner),
) -> FileResponse:
    """A file on our mail: `ours` is one we attached, `email` one that arrived
    on a delivery nobody could place, in whichever store holds it."""
    if source == "ours":
        row = db.get(OpsMailAttachment, attachment_id)
        if row is None:
            raise HTTPException(404, "No such file.")
        if row.message_id is None:
            if row.uploaded_by != user.id:
                raise HTTPException(404, "No such file.")
        else:
            msg = db.get(OpsMessage, row.message_id)
            # Drafts are their author's own, files included.
            if msg is not None and msg.state == "draft" and msg.author_id != user.id:
                raise HTTPException(404, "No such file.")
        return _serve(row.filename, row.content_type, row.path, "", bool(inline))

    if source == "email":
        def look(store_db: Session) -> tuple[str, str, str, str] | None:
            row = (
                store_db.query(EmailAttachment)
                .join(EmailEnvelope, EmailEnvelope.id == EmailAttachment.envelope_id)
                .join(InboundEmail, InboundEmail.id == EmailEnvelope.receipt_id)
                .filter(EmailAttachment.id == attachment_id, InboundEmail.outcome == "unresolved")
                .one_or_none()
            )
            if row is None:
                return None
            return row.filename, row.content_type, row.path, row.refused or ""

        for _slug, found in ops_inbox._each(look):
            if found is not None:
                return _serve(*found, bool(inline))
        raise HTTPException(404, "No such file.")

    raise HTTPException(404, "Unknown source.")
