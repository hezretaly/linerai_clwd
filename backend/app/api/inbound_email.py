"""Mail coming back in, from the Cloudflare Worker.

The only endpoints in this app that no session guards, so the HMAC is the
whole door: without it anyone who finds the URL can write into a buyer's
history.

Two ways in, one pipeline. **The raw path** (`/api/emails/inbound/raw`) takes
the message exactly as the mail server handed it to Cloudflare -- every
recipient, the HTML, every file -- keeps those bytes on disk as the
authoritative copy, and reads them with `app/email_mime.py`. **The JSON path**
(`/api/emails/inbound`) is what the Worker posted before, a digest parsed at
the edge; it stays for as long as that Worker may still be deployed, and it is
honest about what it lost -- a file it named but did not carry is kept as a
row that says so.

Every delivery leaves an ``inbound_emails`` receipt whatever happens to it,
including the ones refused. A 401 into the void is unfalsifiable -- the
operator sees no reply arriving and has no way to tell a broken signature from
a broken Cloudflare route from a buyer who never wrote back. The receipts are
what the setup page reads.

A reply lands as an activity a rep reads, and -- **only if every brake in
`app/email_agent.py` says so** -- Liner may answer it. It says no by default:
`EMAIL_AGENT` is unset and the runtime flag is off, so on an ordinary
deployment nothing here wakes the agent at all. The brakes shipped a phase
before the thing they stop, because there must never be a build where Liner can
send mail on its own and cannot be stopped.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import tempfile
from contextlib import nullcontext
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import email_addresses, email_envelopes, email_files, email_mime, email_reply, mailboxes, matching
from app.config import BACKEND_DIR, settings
from app.db import SessionLocal, active_store, get_db, utcnow
from app.email_addresses import Recipient
from app.escalations import owner_of
from app.events import emit
from app.integrations.email.base import bare_address
from app.email_intake import (
    as_text,
    automated_reason,
    display_name,
    just_the_reply,
    REPLY_RE,
    is_ours,
    sender_address,
    signature_name,
)
from app.models import (
    Appointment,
    CapturedField,
    Conversation,
    EmailAttachment,
    EmailEnvelope,
    Escalation,
    InboundEmail,
    Lead,
    Outreach,
)
from app.models.base import new_id

router = APIRouter(tags=["email"])

#: The JSON digest's ceiling. The old Worker set no limit of its own -- an
#: earlier comment here said it matched one, and it never did -- so this is
#: simply what a digest with no file bytes in it should never approach. The
#: gate posts eleven megabytes and expects a 413, and the raw path below is
#: where a large message goes now.
MAX_BODY = 10 * 1024 * 1024

#: The raw message's ceiling: whole, attachments included. Cloudflare refuses
#: inbound mail over 25 MiB before any Worker runs, so this is headroom rather
#: than a limit anybody should meet; it sits under nginx's 32m so that the app,
#: not the proxy, is the one that answers 413 -- the Worker turns that into a
#: bounce the sender can read. Kept equal to `MAX_RAW` in the Worker's source.
MAX_RAW = 30 * 1024 * 1024

#: Where raw messages live, one folder per store (`email_files.scope_for`),
#: named by receipt id. On disk and never in a table, for the reason the
#: attachments are: a table growing by megabytes a row ruins every backup.
MAIL_ROOT = BACKEND_DIR / "var" / "mail"

#: What a file row says when the relay forwarded the name and not the bytes.
#: The deployed JSON Worker does exactly that, and a list that silently dropped
#: the file would read as a message with nothing attached.
RELAY_KEPT_NAME_ONLY = (
    "The mail relay forwarded only this file's name. Deploy the current Worker "
    "to receive files."
)

# Prefix on a message id this app invented because the mail carried none.
# It has to be recognisable: a synthetic id is a dedupe key and nothing
# else, and must never be echoed back out as an In-Reply-To header naming
# a message that does not exist.
SYNTHETIC = "sha256:"


class InboundBody(BaseModel):
    """What a JSON Worker posts. Every field is optional *and nullable*.

    `str = ""` was wrong, and wrong in the way that costs real mail. A Worker
    writes `inReplyTo: parsed.inReplyTo ?? null` because that is the obvious
    way to say "there wasn't one", and pydantic rejected the null as a type
    error -- so every reply that was not itself threaded came back 400
    malformed. Worse, a sensible Worker treats 4xx as "my payload is wrong,
    retrying will not help" and gives up, which turns a schema quibble into a
    buyer's reply that is gone for good.

    Nothing downstream reads these attributes anyway: every value is pulled
    out of `model_dump()` and coerced. The declarations are documentation of
    the shape, so they must not be stricter than the wire -- which is why the
    lists are `Any`: postal-mime writes an address as `{name, address}`, a
    group as `{name, group: [...]}`, and a hand-written sender a plain string.
    """

    model_config = {"extra": "allow"}

    messageId: str | None = ""
    from_: str | None = ""
    to: str | None = ""
    subject: str | None = ""
    text: str | None = ""
    html: str | None = ""
    inReplyTo: str | None = ""
    receivedAt: str | None = ""
    # The header From, which is the person. `from` above is the envelope
    # sender -- a bounce address or an SRS rewrite as often as not.
    fromAddress: str | None = ""
    fromName: str | None = ""
    # Read when a sender includes them. The deployed Worker sends
    # `references`, `date` and file names; a newer JSON sender may send the
    # header lists too.
    toList: Any = None
    cc: Any = None
    replyTo: Any = None
    references: Any = None
    date: Any = None
    attachments: Any = None
    headers: Any = None


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


def _refuse_unauthenticated(db: Session, signature: str, shared: str) -> None:
    """Record the refusal, then refuse.

    Recorded before refusing: a wrong shared secret is the single most likely
    reason mail stops arriving, and it is invisible otherwise. Naming which
    headers arrived turns "nothing works" into "the Worker is sending the
    header I am not reading".
    """
    offered = [
        name for name, value in (
            ("X-Liner-Signature", signature),
            ("X-Webhook-Secret", shared),
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


def _no_secret() -> None:
    # No secret configured means no door at all, so the endpoint is shut rather
    # than open. An unauthenticated mail intake is worse than a missing one.
    if not settings.webhook_secret:
        raise HTTPException(
            503,
            "WEBHOOK_SECRET is not set, so inbound mail cannot be authenticated "
            "and is refused. See integrations/email/worker/README.md.",
        )


def _identity(envelope_from: str, header_from: str, shown: str) -> str:
    """Who wrote this, as the receipt stores it: the header From, named.

    **The header From is the person; the envelope sender is the route.** A
    forwarded message arrives from an SRS rewrite, a message sent through a
    mailing service from its bounce address, a bounce from `<>` -- none of
    which is the buyer, and matching or minting a lead from one is how a real
    person became a stranger or a robot. So the header wins wherever it was
    sent, and the envelope is the fallback for a sender that did not send it.

    The display name comes over separately as `fromName`, because a mail
    server puts no name in an envelope and the Worker sends addresses bare --
    so every buyer who wrote in arrived unnamed, on real mail, while the tests
    that put a display name in `from` passed. Rebuilt here into the one string
    the rest of this file reads. Not with `formataddr`, which RFC 2047-encodes
    a non-ASCII name: `Jörg` would reach the lead's name column as
    `=?utf-8?b?SsO2cmc=?=`.
    """
    sender = (header_from or envelope_from or "").strip()
    shown = (shown or "").strip()
    if shown and "<" not in sender:
        sender = email_mime.display(Recipient(shown, sender_address(sender) or sender))
    return sender


def _route_note(envelope_from: str, sender: str) -> str:
    """The envelope sender, for the receipt, when it is not the person.

    Kept for the day somebody asks why a message filed under a buyer came
    from `bounces+123@mailer.example` -- the answer is on the receipt rather
    than nowhere.
    """
    route = bare_address(envelope_from) or (envelope_from or "").strip()
    if not route or route.lower() == sender_address(sender):
        return ""
    return f" Envelope sender {route}."


# Two paths, one handler. `/api/emails/inbound` is what the deployed Worker
# posts to; `/api/inbound-email` is what this app documented first. Changing
# either would mean a redeploy on one side to fix a rename on the other, and
# an alias costs a line.
@router.post("/inbound-email")
@router.post("/emails/inbound")
async def receive(
    request: Request,
    background: BackgroundTasks,
    x_liner_signature: str = Header(default=""),
    x_webhook_secret: str = Header(default=""),
    db: Session = Depends(get_db),
) -> dict:
    raw = await request.body()

    # An intake no session guards is an intake anyone can point a firehose at.
    # The JSON digest carries no file bytes, so ten megabytes is far beyond
    # anything honest; refusing before the body is parsed is the point.
    if len(raw) > MAX_BODY:
        _receipt(db, outcome="malformed",
                 detail=f"Body was {len(raw)} bytes; the limit is {MAX_BODY}.")
        raise HTTPException(413, "Payload too large")

    _no_secret()
    proved_by = authenticate(raw, x_liner_signature or "", x_webhook_secret or "")
    if not proved_by:
        _refuse_unauthenticated(db, x_liner_signature or "", x_webhook_secret or "")

    try:
        payload = InboundBody.model_validate_json(raw)
    except Exception as exc:
        _receipt(db, outcome="malformed", detail=str(exc)[:500])
        raise HTTPException(400, "Malformed payload") from None

    data = payload.model_dump()
    message_id = str(data.get("messageId") or "").strip()
    if not message_id:
        # Not every message carries a Message-ID header, and without one the
        # dedupe below has nothing to key on -- while a Worker that retries a
        # dropped response would file the same reply twice.
        #
        # The bytes are the key instead, and that is exact rather than a
        # heuristic: a retry re-posts the identical body, so the digest
        # matches. Two genuinely separate emails do not collide, because the
        # Worker stamps `receivedAt` per invocation at millisecond resolution
        # -- a buyer writing "yes" twice produces two different bodies and
        # stays two messages. Prefixed so a receipt says plainly that this is
        # ours and not something the sender chose.
        message_id = SYNTHETIC + sha256(raw).hexdigest()[:32]
    # The Worker sends `from`, which is a Python keyword; pydantic keeps it in
    # the extras rather than on the field named from_.
    envelope_from = str(data.get("from") or data.get("from_") or "").strip()
    sender = _identity(
        envelope_from, str(data.get("fromAddress") or ""), str(data.get("fromName") or "")
    )
    to = str(data.get("to") or "").strip()
    subject = str(data.get("subject") or "").strip()
    # Plain text when there is any; otherwise the HTML part with its tags
    # taken off, which beats storing markup in a field rendered as text.
    body = str(data.get("text") or "").strip() or as_text(str(data.get("html") or ""))
    in_reply_to = str(data.get("inReplyTo") or "").strip()
    # The Worker already pulls the token out of the recipient. Reading it here
    # too means the deployed Worker needs no edit -- it calls the field
    # `conversationId`, which is what it was named before the token moved onto
    # the send. Either name, or neither: the address is re-parsed regardless.
    hinted = str(data.get("replyToken") or data.get("conversationId") or "").strip()

    # The token the Worker found, folded into the address the resolver reads,
    # so one code path handles both. A hint that disagrees with the address
    # loses: the address is what the mail server actually delivered to.
    if hinted and not REPLY_RE.search(to):
        to = f"reply+{hinted}@{settings.sending_domain or 'hinted'}"

    files = _json_files(data.get("attachments"))
    headers = data.get("headers") if isinstance(data.get("headers"), dict) else {}
    meta = {
        "kind": "json",
        "to": _json_people(data.get("toList")),
        "cc": _json_people(data.get("cc")),
        "reply_to": _json_people(data.get("replyTo")),
        "html": str(data.get("html") or ""),
        "references": " ".join(email_mime.message_ids(_as_text(data.get("references")))),
        "date": _json_date(data.get("date")),
        "importance": _json_importance(headers),
        "files": files,
    }

    # **Which dealership this is for.** The Worker posts to one URL with no
    # store in the path, so on a host serving several dealerships the
    # envelope decides: `alsbou@` is Alsbou's mailbox and `reply+<token>@`
    # was minted by exactly one store's send. Everything else stays with the
    # default store, which is what it always was. The request's own session
    # is the default store's, so a routed delivery opens the right one here
    # and hands the slug to the background pass, which sets it for itself --
    # the middleware's ContextVar is gone by the time that runs.
    slug = mailboxes.store_for(to)
    envelope = {
        "message_id": message_id, "from_address": sender, "to_address": to,
        "subject": subject, "body": body, "in_reply_to": in_reply_to,
    }
    refused = automated_reason(sender, headers, body, files=len(files))
    # The routed store's session is closed however this ends -- a `with`, not
    # a close on the two happy returns, because the one path that raises
    # between the open and the return is the one that leaks a connection per
    # delivery. The request's own session is left to its dependency.
    with (SessionLocal(slug) if slug else nullcontext(db)) as store_db:
        return _claim(store_db, slug, background, proved_by, envelope, refused,
                      meta=meta, note=_route_note(envelope_from, sender))


# The raw path. Same two spellings as the JSON one, so whichever base URL a
# deployment configured, `/raw` on the end of it is right.
@router.post("/inbound-email/raw")
@router.post("/emails/inbound/raw")
async def receive_raw(
    request: Request,
    background: BackgroundTasks,
    x_liner_signature: str = Header(default=""),
    x_webhook_secret: str = Header(default=""),
    x_envelope_from: str = Header(default=""),
    x_envelope_to: str = Header(default=""),
    db: Session = Depends(get_db),
) -> dict:
    """One message, exactly as the mail server handed it to Cloudflare.

    `message/rfc822` in the body; the SMTP envelope in `X-Envelope-From` and
    `X-Envelope-To`, because the envelope is not in the message -- and the
    envelope recipient is what carries `reply+<token>@` and decides which
    dealership's store this is.

    Answered in milliseconds whatever the size: the headers are read to
    dedupe and route, the bytes go to disk, the receipt is claimed, and the
    whole message is parsed in the background. The file is written **before**
    the answer, because the answer is what tells the Worker it may forget the
    message; after that, the copy on disk is the only one.
    """
    _no_secret()
    shared = x_webhook_secret or ""
    raw = await _read_capped(request, MAX_RAW)
    if raw is None:
        # Written only when the caller has already proved itself with the
        # shared secret, which can be checked without the body. Otherwise an
        # oversize refusal would be a way for anyone to grow this table --
        # and a Worker whose message this was turns the 413 into a bounce the
        # sender reads, so the refusal is not silent either way.
        if shared and hmac.compare_digest(settings.webhook_secret, shared):
            _receipt(
                db, outcome="malformed",
                from_address=(x_envelope_from or "")[:255], to_address=(x_envelope_to or "")[:255],
                detail=(
                    f"The message was over {MAX_RAW // (1024 * 1024)} MB and was refused; "
                    "the relay bounces it back to the sender with that reason."
                ),
            )
        raise HTTPException(413, f"Message too large; the limit is {MAX_RAW} bytes.")

    proved_by = authenticate(raw, x_liner_signature or "", shared)
    if not proved_by:
        _refuse_unauthenticated(db, x_liner_signature or "", shared)
    if not raw.strip():
        _receipt(db, outcome="malformed", detail="The relay posted an empty message.")
        raise HTTPException(400, "Empty message")

    head = email_mime.parse_headers(raw)
    # The same fallback the JSON path uses, over the message's own bytes: a
    # retry posts them again unchanged, so the digest is a dedupe key, and
    # two different messages cannot share one.
    message_id = head.message_id or SYNTHETIC + sha256(raw).hexdigest()[:32]
    envelope_from = (x_envelope_from or "").strip()
    sender = head.from_header() or envelope_from
    # The envelope recipient, which is what the mail server delivered to. A
    # caller that did not say falls back to the header's first To, so a
    # message posted by hand still routes somewhere rather than nowhere.
    to = (x_envelope_to or "").strip() or (head.to[0].address if head.to else "")

    slug = mailboxes.store_for(to)
    envelope = {
        "message_id": message_id[:200], "from_address": sender[:255], "to_address": to[:255],
        "subject": head.subject[:255], "body": "", "in_reply_to": head.in_reply_to[:200],
    }
    with (SessionLocal(slug) if slug else nullcontext(db)) as store_db:
        # No verdict on "is this a machine" yet: that needs the body, which is
        # read in the background. `_file_envelope` makes it there.
        return _claim(store_db, slug, background, proved_by, envelope, "",
                      raw=raw, note=_route_note(envelope_from, sender))


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """The body, or None the moment it passes `limit`.

    A declared length over the limit is refused before a byte is read; one
    that lies, or a chunked body with none, is cut off as it streams. Either
    way thirty megabytes is the most this holds in memory for one request.
    """
    declared = request.headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > limit:
        return None
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > limit:
            return None
    return bytes(buffer)


def _claim(db: Session, slug: str, background: BackgroundTasks, proved_by: str,
           envelope: dict, refused: str, *, meta: dict | None = None,
           raw: bytes | None = None, note: str = "") -> dict:
    """Dedupe, claim, and hand the delivery to the background pass."""
    message_id = envelope["message_id"]
    # Idempotent on the message id -- the sender's when there is one, the
    # digest of the bytes when there is not. Cloudflare retries, and a retry
    # must not give a buyer two replies on their timeline.
    if message_id:
        seen = (
            db.query(InboundEmail)
            .filter(
                InboundEmail.message_id == message_id,
                # 'received' counts: it is a delivery already claimed and still
                # being filed. Checking only for 'accepted' would let a fast
                # retry slip past into a second activity.
                InboundEmail.outcome.in_(("received", "accepted", "unresolved")),
            )
            .first()
        )
        if seen is not None:
            _receipt(db, outcome="duplicate", detail=f"Already accepted as {seen.id}.",
                     **envelope)
            return {"ok": True, "outcome": "duplicate"}

    # The store this delivery is filed in, named rather than left implicit:
    # the background pass runs after the request's own store has been
    # forgotten, and a prefixed intake URL that the envelope did not route
    # would otherwise be filed into the default store's file.
    store = slug or active_store()
    receipt_id = new_id()
    if raw is not None:
        # On disk before the answer. Once this returns 200 the Worker drops
        # its copy, so a message whose bytes were not kept by then is a
        # message lost. A failure here is a 503, which the Worker retries;
        # the receipt says why, and does not count as a claim.
        try:
            relative = save_raw(store, receipt_id, raw)
        except OSError as exc:
            _receipt(db, outcome="failed",
                     detail=f"The message could not be saved to disk, so it was not accepted: {exc}"[:500],
                     **envelope)
            raise HTTPException(503, "Could not store the message; retry later.") from None
        meta = {"kind": "raw", "raw_path": relative}

    # Claim the message before answering, then do the work after. Returning
    # 200 fast matters: the Worker rejects the message to the sender on a
    # non-2xx, so a slow CRM bounces a real buyer's reply.
    #
    # The claim is why this is written down rather than just backgrounded. A
    # plain "return 200, process later" loses the dedupe it is sitting right
    # next to -- a retry arriving mid-processing finds no accepted receipt and
    # files the reply twice. The row goes in first, holding the message id;
    # the background pass fills in what it resolved to.
    claim = _receipt(db, id=receipt_id, outcome="received",
                     detail=f"Authenticated by {proved_by}.{note}", **envelope)
    # `refused` was decided back in the request, where the headers still
    # exist -- `extra="allow"` keeps whatever the Worker sent, and a header is
    # a sender declaring itself a machine, which is the only loop-breaker that
    # stops a vacation responder on its first turn. It is handed over as an
    # argument rather than stored: `create_all` adds a table to a database
    # that already exists and never a column.
    background.add_task(_place, claim.id, refused, store, meta)
    return {"ok": True, "outcome": "received", "receipt_id": claim.id, "store": slug}


# ------------------------------------------------------------ the raw file


def save_raw(store: str, receipt_id: str, raw: bytes) -> str:
    """Write one message to `var/mail/<scope>/<receipt_id>.eml`; return the
    path relative to `var/mail/`.

    Written to a temporary file and renamed into place, so a reader never
    finds half a message.
    """
    relative = f"{email_files.scope_for(store)}/{receipt_id}.eml"
    target = MAIL_ROOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return relative


def raw_path(relative: str) -> Path | None:
    """The file behind a stored relative path, checked to be inside
    `var/mail/` -- a row is not an authority on where the disk is."""
    if not relative:
        return None
    candidate = (MAIL_ROOT / relative).resolve()
    try:
        candidate.relative_to(MAIL_ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _raw_for(receipt_id: str) -> str:
    """The saved message for a receipt that has no envelope yet, if any.

    Looked for in every scope rather than only the current store's: a receipt
    id is unique, and the store a message was saved under is a fact about the
    request that saved it, which a later re-placement does not have.
    """
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", receipt_id or ""):
        return ""
    if not MAIL_ROOT.is_dir():
        return ""
    for found in MAIL_ROOT.glob(f"*/{receipt_id}.eml"):
        return found.relative_to(MAIL_ROOT).as_posix()
    return ""


# --------------------------------------------------- the JSON digest's parts


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value if v)
    return str(value)


def _json_people(value: Any) -> list[dict]:
    """A postal-mime address list -- or a string, or a list of strings -- as
    `{name, address}` dicts, groups flattened."""
    out: list[Recipient] = []

    def add(item: Any) -> None:
        if isinstance(item, dict):
            if isinstance(item.get("group"), list):
                for member in item["group"]:
                    add(member)
                return
            address = str(item.get("address") or "").strip()
            if "@" in address:
                out.append(Recipient(name=str(item.get("name") or "").strip(), address=address))
        elif isinstance(item, str):
            out.extend(email_addresses.from_header_value(item))

    for item in value if isinstance(value, list) else [value]:
        if item:
            add(item)
    seen: set[str] = set()
    kept = []
    for r in out:
        if r.key not in seen:
            seen.add(r.key)
            kept.append(r.as_dict())
    return kept


def _json_date(value: Any) -> str:
    """The `Date:` postal-mime sends -- ISO 8601, or the header verbatim when
    it could not read it -- as naive-UTC ISO, or "" when neither parses."""
    text = _as_text(value).strip()
    if not text:
        return ""
    when: datetime | None = None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return ""
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    return when.isoformat()


def _json_importance(headers: dict) -> str:
    lowered = {str(k).lower(): str(v or "").strip().lower() for k, v in (headers or {}).items()}
    if lowered.get("importance", "").startswith("high") or lowered.get("x-priority", "")[:1] in ("1", "2"):
        return "high"
    return "normal"


def _json_files(value: Any) -> list[dict]:
    """The files a JSON digest named, as plain dicts.

    The deployed Worker sends `{filename, mimeType, size}` and never the
    bytes. A sender that includes `content` (base64) gets its file kept; one
    that does not gets a row saying the name is all that arrived.
    """
    out = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        out.append({
            "filename": str(item.get("filename") or ""),
            "content_type": str(item.get("mimeType") or item.get("contentType") or "application/octet-stream"),
            "size": int(item.get("size") or 0) if str(item.get("size") or "0").isdigit() else 0,
            "content": item.get("content") if isinstance(item.get("content"), str) else "",
            "content_id": str(item.get("contentId") or "").strip().strip("<>"),
            "disposition": "inline" if item.get("disposition") == "inline" else "attachment",
        })
    return out


# ------------------------------------------------------------- placement


def _place(receipt_id: str, refused: str = "", slug: str = "", meta: dict | None = None) -> None:
    """Resolve one claimed delivery and store it. Runs after the response.

    Its own session: the request's is closed by the time this runs, and
    reusing it is the classic background-task crash. And its own store:
    `slug` is the dealership the envelope was routed to, set here for the
    whole pass so every helper below -- the lead matcher, the reply agent,
    the emit -- reads and writes that store's file rather than the default.

    `refused` is `automated_reason`'s verdict, decided back in the request
    where the headers still exist and handed over as an argument. It is not a
    column because it cannot be one: `create_all` adds a table to a database
    that already exists and never a column, and there is no Alembic here by
    design. It is empty when a lead already exists, since the question only
    arises for a delivery that matched nobody. A raw message has its verdict
    made here instead, once its body has been read.

    `meta` is what the request knew that the receipt has no column for: the
    JSON digest's recipient lists and file names, or where the raw message
    was saved. `claim_unresolved` re-places with none, and needs none -- the
    envelope was stored the first time.
    """
    with mailboxes.using(slug):
        _place_in_store(receipt_id, refused, meta)


def _place_in_store(receipt_id: str, refused: str, meta: dict | None = None) -> None:
    db = SessionLocal()
    try:
        claim = db.query(InboundEmail).filter_by(id=receipt_id).one_or_none()
        if claim is None or claim.outcome != "received":
            return

        # What the message carried beyond one address and a body, stored
        # before anything is decided about it -- so mail nobody can place
        # keeps its recipients and its files exactly as placed mail does.
        env, read_verdict = _file_envelope(db, claim, meta)
        if read_verdict is not None and not refused:
            refused = read_verdict
        files = _file_count(db, env)

        lead, outreach, matched_by = _resolve(
            db, claim.to_address, claim.in_reply_to, claim.from_address,
            env.references if env is not None else "",
        )
        if lead is None:
            # Nobody here yet. Somebody writing to sales@ before they are a
            # lead is a buyer arriving through the door this dealership
            # publishes, so they become one -- but only if a person sent it.
            lead, why_not = _lead_from(db, claim, refused)
            # Which rule filed it. Blank would say "the From address matched an
            # existing lead", which is the opposite of what happened -- and
            # `matched_by` exists precisely so a rep looking at a misfiled
            # reply can see what put it there.
            matched_by = "new_lead" if lead is not None else matched_by
        if lead is None:
            claim.outcome = "unresolved"
            notes = _notes(claim.detail)
            claim.detail = (
                f"Not filed against anyone: {why_not} Kept rather than dropped -- "
                "something really arrived, and a delivery nobody can place is the "
                "only way to tell a filtered sender from a broken mail route."
                + (f" {notes}" if notes else "")
            )
            db.commit()
            # Emitted as well as the accepted case, so the mailbox updates
            # itself for a stranger too. Without this the one delivery nobody
            # is expecting -- no buyer page, no timeline, visible only on
            # /app/email -- is also the one that needs a manual refresh to
            # appear. The event is only ever raised after the HMAC passed;
            # refusals stay silent so an unauthenticated caller cannot grow
            # the events table.
            emit(db, "email.received", {
                "lead_id": None, "outreach_id": None,
                "receipt_id": claim.id, "matched_by": "", "outcome": "unresolved",
                "attachments": files,
            })
            return

        # Every accepted delivery, not only the one that minted them. A buyer
        # resolved by token or by address never went through `_lead_from`, so
        # a lead that arrived nameless stayed nameless for good however many
        # named messages followed. It fills a blank and never overwrites.
        name_from_delivery(db, lead, claim)

        record = Outreach(
            lead_id=lead.id,
            appointment_id=outreach.appointment_id if outreach else None,
            channel="email",
            direction="in",
            kind="reply",
            to_address=claim.from_address,
            subject=claim.subject,
            # Trimmed here rather than on the way in: the receipt above still
            # holds every byte that arrived, so nothing is lost if a quote
            # marker ever fires on something it should not.
            body=just_the_reply(claim.body),
            provider="inbound",
            # Only a real one. A synthetic id is ours, and putting it here
            # would send it back out as an In-Reply-To header naming a message
            # that never existed.
            provider_message_id=(
                None if claim.message_id.startswith(SYNTHETIC) else claim.message_id
            ) or None,
            in_reply_to=claim.in_reply_to or None,
            status="sent",
            # When it arrived, not when it was filed. The two are the same
            # moment on the live path and days apart when `claim_unresolved`
            # re-places a stranger's mail onto the buyer they turned out to
            # be -- which put a week-old email at the top of their timeline.
            # Not the `Date:` header either: that is the sender's clock, and
            # ordering their reply against our own send by two different
            # clocks is how an answer appears above its question.
            sent_at=claim.created_at or utcnow(),
        )
        db.add(record)
        db.flush()

        # **The thread exists from the first email, not the third exchange.**
        # It used to be minted only when Liner *replied*, so a buyer who wrote
        # in and was not answered had no conversation at all -- no Take over,
        # no composer, no row in `/app/conversations` -- and the one person who
        # could have helped had to find them on a diagnostics tab. Email is a
        # channel like any other here, and the dashboard is organised by buyer.
        #
        # The buyer's message is mirrored into it carrying `outreach_id`, the
        # same mechanism an appointment confirmation uses: `app/timeline.py`
        # folds a mirror and its outreach row into one entry, so this costs no
        # duplicate on the buyer's page. Without the mirror the thread would
        # exist and read as empty -- `/api/conversations` lists a conversation
        # only once a buyer has actually said something in it, which is what
        # stops an opened-and-closed chat widget reaching a rep.
        convo = email_reply.thread_for(db, lead)
        email_reply.remember_inbound(db, convo, record, just_the_reply(claim.body))

        # Before the receipt is stamped, not after. `accepted` is what the
        # setup page shows and what the gate waits on, so anything still
        # outstanding when it appears is a race -- the receipt reads filed
        # while the escalation this reply reopens is still sitting claimed.
        # It failed roughly one run in three that way, which is the worst
        # kind: rare enough to look like a fluke.
        reopened = _reopen(db, outreach, lead)

        claim.outcome = "accepted"
        claim.matched_by = matched_by
        claim.lead_id = lead.id
        claim.outreach_id = record.id
        db.commit()

        emit(db, "email.received", {
            "lead_id": lead.id, "outreach_id": record.id,
            "receipt_id": claim.id, "matched_by": matched_by,
            "outcome": "accepted", "reopened": reopened, "attachments": files,
        })

        # And, last, Liner may answer -- if every brake in `email_agent` says
        # so, and by default none of them does. It is *queued* rather than
        # sent: every reply waits `EMAIL_REPLY_COOLDOWN_MINUTES`, which stops
        # a dealership answering three seconds after a buyer wrote and gives a
        # rep a window to take the thread over first. `app/email_replies.py`
        # drains it.
        #
        # After the receipt is stamped and after the event, so a failure here
        # leaves the delivery filed and visible rather than taking the whole
        # placement down with it. Off by default, so this is a no-op on any
        # deployment that has not deliberately turned it on.
        try:
            answered = email_reply.schedule(
                db, claim, lead, record, automated=refused
            )
            if not answered.get("queued") and answered.get("reason") not in (
                "", "off_in_env", "switched_off",
            ):
                # Recorded on the receipt, because "Liner did not reply" is
                # otherwise indistinguishable from "Liner is off" and from a
                # provider that refused -- three different problems.
                claim.detail = f"{claim.detail} Liner did not reply: {answered['detail']}"
                db.commit()
        except Exception as exc:  # a failed reply must not lose the delivery
            claim.detail = f"{claim.detail} Liner's reply failed: {exc}"[:500]
            db.commit()
    except Exception as exc:  # never let a background failure vanish
        db.rollback()
        claim = db.query(InboundEmail).filter_by(id=receipt_id).one_or_none()
        if claim is not None:
            claim.outcome = "failed"
            claim.detail = f"Could not be filed: {exc}"[:500]
            db.commit()
    finally:
        db.close()


def _notes(detail: str) -> str:
    """What the claim's detail said beyond "Authenticated by ..." -- the
    envelope sender, a part that could not be read -- so the unresolved
    explanation that replaces it does not throw those away."""
    return re.sub(r"^\s*Authenticated by [\w-]+\.\s*", "", detail or "").strip()


def _file_count(db: Session, env: EmailEnvelope | None) -> int:
    """Files a person would count: not the logo drawn inside the HTML."""
    if env is None:
        return 0
    return (
        db.query(EmailAttachment)
        .filter(
            EmailAttachment.envelope_id == env.id,
            ~((EmailAttachment.disposition == "inline") & (EmailAttachment.content_id != "")),
        )
        .count()
    )


def _file_envelope(
    db: Session, claim: InboundEmail, meta: dict | None
) -> tuple[EmailEnvelope | None, str | None]:
    """Store what this delivery carried beside its receipt, once.

    **Idempotent, because it runs more than once.** A placement that is
    retried, and `claim_unresolved` re-placing a stranger's mail onto the
    buyer they turned out to be, both arrive here for a receipt whose envelope
    already exists -- and a second set of file rows would show every
    attachment twice. The envelope and its files are committed together, so
    finding the envelope is finding all of it.

    Returns the envelope, and -- for a raw message, whose body is read only
    now -- `automated_reason`'s verdict on it (None when there was nothing
    new to judge).
    """
    existing = email_envelopes.for_receipt(db, claim.id)
    if existing is not None:
        return existing, None
    meta = meta or {}
    scope = email_files.scope_for(active_store())

    # A JSON digest has no raw message; anything else -- the raw path, or a
    # re-placement that was handed nothing -- may have one on disk.
    relative = meta.get("raw_path") or ("" if meta.get("kind") == "json" else _raw_for(claim.id))
    if relative:
        path = raw_path(relative)
        if path is None:
            raise RuntimeError(f"the saved message var/mail/{relative} is missing from disk")
        data = path.read_bytes()
        parsed = email_mime.parse(data)
        # The receipt's own columns, now that the body has been read. The
        # untrimmed text: the receipt is the record of what arrived, and the
        # trim happens on the way to the timeline.
        claim.body = parsed.text
        claim.subject = (parsed.subject or claim.subject or "")[:255]
        claim.from_address = (parsed.from_header() or claim.from_address or "")[:255]
        claim.in_reply_to = (parsed.in_reply_to or claim.in_reply_to or "")[:200]
        if parsed.problems:
            claim.detail = f"{claim.detail} {' '.join(parsed.problems)}"[:1000]
        sender = parsed.from_
        env = EmailEnvelope(
            receipt_id=claim.id,
            rfc_message_id=parsed.message_id,
            in_reply_to=parsed.in_reply_to,
            references=parsed.references,
            from_name=(sender.name if sender else "")[:255],
            from_address=(sender.address if sender else "")[:320],
            to_json=email_addresses.dumps(parsed.to),
            cc_json=email_addresses.dumps(parsed.cc),
            reply_to_json=email_addresses.dumps(parsed.reply_to),
            html=parsed.html,
            importance=parsed.importance,
            dated_at=parsed.dated_at,
            raw_path=relative,
            size=len(data),
        )
        db.add(env)
        db.flush()
        for part in parsed.parts:
            _keep_part(db, env, scope, part)
        verdict = automated_reason(
            claim.from_address, parsed.headers, parsed.text,
            files=sum(1 for p in parsed.parts if p["disposition"] != "inline"),
        )
        db.commit()
        return env, verdict

    if meta.get("kind") == "json":
        sender = claim.from_address or ""
        dated = meta.get("date") or ""
        env = EmailEnvelope(
            receipt_id=claim.id,
            rfc_message_id=("" if claim.message_id.startswith(SYNTHETIC) else claim.message_id)[:255],
            in_reply_to=(claim.in_reply_to or "")[:255],
            references=meta.get("references") or "",
            from_name=display_name(sender)[:255],
            from_address=(bare_address(sender) or sender)[:320],
            to_json=_json_list(meta.get("to")),
            cc_json=_json_list(meta.get("cc")),
            reply_to_json=_json_list(meta.get("reply_to")),
            html=meta.get("html") or "",
            importance=meta.get("importance") or "normal",
            dated_at=datetime.fromisoformat(dated) if dated else None,
        )
        db.add(env)
        db.flush()
        for item in meta.get("files") or []:
            _keep_named(db, env, scope, item)
        db.commit()
        return env, None

    # A receipt from before envelopes were kept, re-placed by
    # `claim_unresolved`. There is nothing more to know about it than the
    # receipt says, and an empty envelope would claim otherwise.
    return None, None


def _json_list(people: list[dict] | None) -> str:
    return json.dumps(people or [])


def _keep_part(db: Session, env: EmailEnvelope, scope: str, part: dict) -> EmailAttachment:
    """One file off a raw message: stored, or kept as a name with the reason.

    **A received file is never refused silently.** An upload refused at the
    composer is a person who can pick another file; the sender of a received
    one cannot be asked again, so a type no provider carries (`.exe`) is kept
    as a row that says so, with no bytes and no link -- one click from a rep is
    exactly where it must not be.
    """
    name = email_files.safe_filename(part.get("filename"))
    data = part.get("data") or b""
    declared = part.get("content_type") or "application/octet-stream"
    row = EmailAttachment(
        envelope_id=env.id,
        filename=name,
        content_type=declared[:120],
        size=len(data),
        content_id=(part.get("content_id") or "")[:255],
        disposition=part.get("disposition") or "attachment",
    )
    reason = email_files.blocked_reason(name)
    if reason:
        row.refused = reason[:300]
    elif not data:
        row.refused = "The file arrived empty."
    else:
        try:
            row.sha256, row.path = email_files.store(scope, data)
            row.content_type = email_files.sniff(data, declared, name)[:120]
        except OSError as exc:
            # One file the disk would not take is that file's problem, not the
            # message's: the rest is filed, and the original is still whole in
            # `var/mail/`.
            row.refused = f"The file could not be saved: {exc}"[:300]
    db.add(row)
    return row


def _keep_named(db: Session, env: EmailEnvelope, scope: str, item: dict) -> EmailAttachment:
    """One file off a JSON digest: its bytes when the sender included them,
    otherwise its name and a row saying that is all that came."""
    data = b""
    if item.get("content"):
        try:
            data = base64.b64decode(item["content"], validate=False)
        except (binascii.Error, ValueError):
            data = b""
    name = email_files.safe_filename(item.get("filename") or "attachment")
    if data:
        return _keep_part(db, env, scope, {**item, "filename": name, "data": data})
    row = EmailAttachment(
        envelope_id=env.id,
        filename=name,
        content_type=(item.get("content_type") or "application/octet-stream")[:120],
        size=int(item.get("size") or 0),
        content_id=(item.get("content_id") or "")[:255],
        disposition=item.get("disposition") or "attachment",
        refused=(email_files.blocked_reason(name) or RELAY_KEPT_NAME_ONLY)[:300],
    )
    db.add(row)
    return row


def name_from_delivery(
    db: Session, lead: Lead, claim: InboundEmail, *, notify: bool = True
) -> str:
    """Give a nameless buyer the name their own mail carries. Returns what it set.

    **Fills a blank and never overwrites one.** A name already on the row came
    from somewhere with more authority than this -- a booking, a rep who typed
    it, a lead document -- while a display name is a free-text field the
    sender's client will put anything in. Letting a later email rename a buyer
    a rep has confirmed is how somebody ends up on the phone to the wrong name,
    which is the failure the whole provenance idea exists to prevent.

    Two rungs, strongest first, the same ladder `_lead_from` runs when it mints
    one -- and it is a function rather than a second copy because that is the
    rule this codebase keeps relearning: two versions of "what is this person
    called" is how the buyer page and the mailbox start disagreeing.

    1. **The envelope's display name.** `"Hezretaly A." <a.hezret@outlook.com>`
       -- a fact the sender's own client asserts about them, not something read
       out of prose. Note it arrives as `fromName` and not inside `from`: a
       mail server puts no display name in an *envelope*, so the header is
       rebuilt at intake before anything reads it.
    2. **A name signed at the bottom.** A guess, so it is also written as a
       `signed_name` captured field with provenance `inferred`, and the field
       is what carries that caveat -- the name column cannot.

    Called on **every** accepted delivery, not only the one that mints the
    buyer. That was the gap: a lead created before any of this existed, or one
    minted from a message whose sender had set no display name, was stuck
    unnamed for good, because `_resolve` finds them and `_lead_from` never runs
    again. Every later email carrying a perfectly good name confirmed nothing.
    """
    if lead.name:
        return ""

    shown = display_name(claim.from_address)
    if shown:
        lead.name = shown
        db.commit()
        if notify:
            emit(db, "lead.updated", {"lead_id": lead.id, "field": "name"})
        return shown

    signed = signature_name(just_the_reply(claim.body))
    if not signed:
        return ""
    # Recorded as inferred whether or not it fills the column, so a rep reading
    # the buyer page can see the name was read out of a sign-off rather than
    # asserted by anyone. Not written twice: they may sign every message.
    already = (
        db.query(CapturedField)
        .filter(
            CapturedField.lead_id == lead.id,
            CapturedField.key == "signed_name",
            CapturedField.value == signed,
        )
        .first()
    )
    if already is None:
        db.add(CapturedField(
            lead_id=lead.id, key="signed_name", value=signed, provenance="inferred",
        ))
    lead.name = signed
    db.commit()
    # `notify` is off while a lead is being minted: `lead.created` follows a
    # line later and says everything this would, and an update announcing a
    # buyer nobody has been told about yet arrives in the wrong order.
    if notify:
        emit(db, "lead.updated", {"lead_id": lead.id, "field": "name"})
    return signed


def _lead_from(db: Session, claim: InboundEmail, refused: str) -> tuple[Lead | None, str]:
    """Mint a buyer from a delivery that matched nobody, or say why not.

    **A person writing to a published address is a buyer arriving.** They used
    the door the dealership advertises, and leaving them as a receipt on a
    diagnostics tab means the one contact nobody expected is also the one
    nobody works. So the address becomes a lead and `claim_unresolved` joins
    everything else they sent.

    **A machine writing to it is not.** The buyer list is the one list here
    that has to mean exactly one thing, and a lead invented from a newsletter
    is worse than a receipt somebody glances at -- it is a name in every
    assignment picker and a row in every queue. `automated_reason` is the test,
    and it is a header check before it is a guess.

    What they are called comes from `name_from_delivery`, which every later
    delivery runs too. Neither rung is ever used to *match*: `app/matching.py`
    stays email exact and phone by its last ten digits, because a name is not
    identity and two Dave Joneses are two people.
    """
    address = sender_address(claim.from_address)
    # **Who they wrote to decides whose they are.** `support@`, `founder@` and
    # `cto@` are Liner's own addresses -- a stranger mailing our support desk
    # is our correspondent, and turning them into a car buyer on somebody
    # else's showroom list is the ops/dealership split failing from the inside.
    # That is not a hypothetical: it is what happened the first time this ran,
    # and the gate caught it because the ops mailbox stopped showing the
    # stranger it is there to show. Everything else -- `sales@`, `reply+` --
    # is the dealership's door.
    if is_ours(claim.to_address):
        return None, (
            f"it was addressed to {claim.to_address or 'one of our own addresses'}, "
            "which is Liner's rather than the dealership's, so it belongs in the "
            "ops mailbox and not on a buyer list."
        )
    if refused:
        return None, f"no lead was created because {refused}."
    if not address:
        return None, "there was no address to create one from."

    # `_resolve` should already have found them, and did not -- so this is the
    # second lock rather than the first. It is here because the cost of the two
    # disagreeing is a duplicate buyer, which is the exact failure
    # `app/matching.py` exists to prevent, arriving through a new door.
    existing = matching.match_lead(db, address, "")
    if existing is not None:
        return existing, ""

    lead = Lead(name="", email=address, phone="", source="email")
    db.add(lead)
    db.flush()
    # The same ladder every later delivery runs, rather than a copy of it here.
    # It used to be written out twice and the two drifted immediately: minting
    # read the envelope and the signature, and nothing else ever read either
    # again, so a buyer who arrived unnamed stayed unnamed however many named
    # messages they sent afterwards.
    name_from_delivery(db, lead, claim, notify=False)
    db.commit()

    # The other half of the ladder, and the reason it is called here rather
    # than left for later: they may have written three times before this one.
    matching.claim_unresolved(db, lead)
    emit(db, "lead.created", {"lead_id": lead.id, "source": "email"})
    return lead, ""


def _resolve(
    db: Session, to: str, in_reply_to: str, sender: str, references: str = ""
) -> tuple[Lead | None, Outreach | None, str]:
    """Who wrote in, by the narrowest rule that fits.

    Order matters. The token is exact and was minted by us; the message id is
    exact but only present when the client threaded properly; the From address
    is the loosest and comes last, because two people can share one.

    **Our own sends are asked first, and by the id the buyer's client actually
    quotes.** `Outreach.provider_message_id` holds Resend's API id, which no
    mail client has ever seen -- so this rung only ever matched *inbound* rows,
    a buyer's earlier message rather than ours. The Message-ID our send really
    went out with is on its envelope (`rfc_message_id`), and a match there
    names the send, its appointment and its lead exactly. A match on one of
    the buyer's own earlier messages is still a clue, but a weaker one, and it
    must never win over one of ours.
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

    def filed(sent: Outreach | None) -> Lead | None:
        if sent is None or not sent.lead_id:
            return None
        return db.query(Lead).filter_by(id=sent.lead_id).one_or_none()

    def by_provider_id(mid: str, direction: str) -> Outreach | None:
        bare = mid.strip().strip("<>")
        return (
            db.query(Outreach)
            .filter(
                Outreach.provider_message_id.in_([mid, bare, f"<{bare}>"]),
                Outreach.direction == direction,
                Outreach.lead_id.is_not(None),
            )
            .order_by(Outreach.created_at.desc())
            .first()
        )

    if in_reply_to:
        for sent in (_our_send(db, in_reply_to), by_provider_id(in_reply_to, "out")):
            lead = filed(sent)
            if lead is not None:
                return lead, sent, "in_reply_to"

    # The rest of the chain, newest first. A buyer answering their own
    # follow-up, or a colleague they copied answering them, quotes a message
    # that is not ours in In-Reply-To -- while References still names the
    # send that started it.
    for mid in reversed(email_mime.message_ids(references)):
        sent = _our_send(db, mid)
        lead = filed(sent)
        if lead is not None:
            return lead, sent, "references"

    if in_reply_to:
        sent = by_provider_id(in_reply_to, "in")
        lead = filed(sent)
        if lead is not None:
            return lead, sent, "in_reply_to"

    # The same matcher the importer, manual entry and book_appointment use. A
    # name is never part of it, so a stranger stays a stranger.
    lead = matching.match_lead(db, sender_address(sender), "")
    if lead is not None:
        return lead, None, "from_address"

    return None, None, ""


def _our_send(db: Session, message_id: str) -> Outreach | None:
    """The send of ours that went out under this Message-ID, if any."""
    env = email_envelopes.by_rfc_message_id(db, message_id, outbound=True)
    if env is None or not env.outreach_id:
        return None
    return db.get(Outreach, env.outreach_id)


def _reopen(db: Session, outreach: Outreach | None, lead: Lead) -> bool:
    """A buyer answering the question a rep asked is the rep's turn again.

    Only reopens an escalation that was already claimed and closed off -- a
    thread nobody had flagged is not made urgent by a reply, and turning every
    inbound message into a queue entry is how the queue stops meaning anything.

    Whose turn it becomes follows `app/escalations.py`: their owner's if they
    have one, and the unclaimed queue only if they do not. A buyer with a rep
    on them does not need a person to be *found*, and dropping them into a
    queue that says so is how a manager ends up assigning somebody who is
    already assigned. The reply is not silent for having an owner -- it is an
    entry on their timeline, a row in `/app/email`, and an `email.received`
    frame on the socket, which between them say what actually happened rather
    than "somebody is needed".
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

    # Back to whoever owns the buyer, not back to the unclaimed pool. A reply
    # is their turn again -- it is not news that this buyer has nobody, and
    # dropping an owned lead into "Needs a person" is the same disagreement
    # `assign_lead` was written to settle, arriving from the other end.
    convo = db.query(Conversation).filter_by(id=claimed.conversation_id).one_or_none()
    owner = owner_of(db, convo) if convo is not None else None
    claimed.claimed_at = utcnow() if owner else None
    claimed.claimed_by_user_id = owner
    db.commit()
    emit(db, "handoff.triggered", {
        "conversation_id": claimed.conversation_id, "action": "buyer_replied",
        "claimed_by_user_id": owner,
    })
    return True
