"""Every email a dealership sends goes through here.

There were five paths that put a dealership's mail on the wire -- the mailbox
composer, the test send, a lead follow-up, an appointment confirmation and
Liner's own reply -- and each had grown its own copy of the send: which
guard it ran, whether it signed, whether it threaded, whether it kept the
provider's thread id. They had drifted exactly the way `outreach_send` says
copies drift. The composer was the only one that sent the signature image,
two of the five dropped `provider_thread_id`, and the buyer summary was
written as a `queued` row and never sent at all.

So one path, in two steps:

* `build` turns what a caller was given -- typed addresses, a subject, text
  or HTML, file ids -- into a checked `Message`, or raises `OutboundError`
  naming what was wrong. Nothing is written until everything that can be
  refused has been.
* `send` writes the row **before** the provider is called, runs the one
  outbound limit over **every** recipient, and records whatever happens next
  on that row. A send that raises still leaves a failed row with the reason:
  "it did not go and here is why" is something a rep can act on, and a 500
  with no row is not.

**One message is one `outreach` row**, however many recipients or files it
carries. The row keeps the primary counterparty (the first To) and the
plain-text half; everything else a real email is -- Cc, Bcc, the HTML, the
threading headers, the files -- is on its `email_envelopes` row, because
`outreach` cannot gain a column.

**Threading ids are RFC Message-IDs, never a provider's.** Resend's `id` is a
UUID and Gmail's is its own; putting either in an `In-Reply-To` names a
message that does not exist, and the buyer's client starts a new thread. So
`In-Reply-To` is only ever set from an id we know the message really carried.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from email.utils import formataddr, parseaddr
from typing import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import email_addresses, email_envelopes, email_html, outreach_send
from app.db import utcnow
from app.email_addresses import MAX_RECIPIENTS, Recipient
from app.email_envelopes import AttachmentError
from app.events import emit
from app.integrations.email.base import EmailSender, SendResult, header_value
from app.integrations.registry import get_email_sender
from app.models import EmailAttachment, EmailEnvelope, InboundEmail, Outreach


class OutboundError(ValueError):
    """Something about the message itself, in words for the person sending it.

    `status` is the HTTP answer an endpoint should give: 400 for anything the
    sender can fix, 404 for a message they named that is not there.
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ threading

#: A delivery with no Message-ID of its own is deduped on a digest of its
#: bytes (`api/inbound_email.py`). That digest is ours, and written into an
#: `In-Reply-To` it names a message that never existed.
_SYNTHETIC = "sha256:"

#: How long a `References` chain may grow. RFC 5322 suggests trimming; the
#: first id is always kept, because it is the conversation's root and the one
#: clients thread on when the middle is missing.
MAX_REFERENCES = 20

_BRACKETED = re.compile(r"<[^<>\s]+>")


@dataclass(frozen=True)
class Thread:
    """Where a message sits: its parent's Message-ID and the chain above it."""

    in_reply_to: str = ""
    references: str = ""


def message_id(value: str | None) -> str:
    """One Message-ID as `<local@domain>`, or "" when `value` is not one.

    Written with the angle brackets whatever it arrived as -- one client quotes
    `<abc@x>` back and a hand-built header says `abc@x`. Anything without an
    `@` is refused rather than wrapped: that is a provider's API id (a Resend
    UUID, `outbox-…`) and it cannot go in a header.
    """
    raw = header_value(value)
    if not raw or raw.startswith(_SYNTHETIC):
        return ""
    found = _BRACKETED.findall(raw)
    candidate = found[0] if found else (
        f"<{raw.strip('<>')}>" if " " not in raw.strip() else ""
    )
    return candidate if "@" in candidate else ""


def message_ids(value: str | None) -> list[str]:
    """Every Message-ID in a `References` (or `In-Reply-To`) value, in order."""
    raw = header_value(value)
    if not raw:
        return []
    found = _BRACKETED.findall(raw) or raw.split()
    return [m for m in (message_id(f) for f in found) if m]


def chain(parent_references: str | None, parent: str | None) -> str:
    """The `References` a reply carries: the parent's chain, then the parent.

    RFC 5322 section 3.6.4. Duplicates are dropped keeping the first place
    each appeared, and a chain past `MAX_REFERENCES` keeps its root and the
    most recent links.
    """
    ids = list(dict.fromkeys(message_ids(parent_references) + [m for m in [message_id(parent)] if m]))
    if len(ids) > MAX_REFERENCES:
        ids = ids[:1] + ids[-(MAX_REFERENCES - 1):]
    return " ".join(ids)


def _parent(mid: str, references: str, fallback_parent: str = "") -> Thread:
    """A reply's thread, given what its parent carried.

    `references` is the parent's own `References`; where it had none, its
    `In-Reply-To` stands in, which is the rule the RFC gives for a parent
    that was itself a first reply.
    """
    parent = message_id(mid)
    if not parent:
        return Thread()
    return Thread(parent, chain(references or fallback_parent, parent))


def thread_under_outreach(db: Session, row: Outreach | None) -> Thread:
    """How to answer one `outreach` row, in either direction.

    **A received message** threads under its own Message-ID: the envelope's,
    else the receipt's, else the id the row was filed with -- never a
    synthetic digest. **One of our own sends** threads under the Message-ID it
    went out with, where the provider reported one; where it did not, there
    is nothing honest to put in the header, and the reply is unthreaded
    rather than threaded under a provider's UUID.
    """
    if row is None or row.channel != "email":
        return Thread()
    env = email_envelopes.for_outreach(db, row)
    if row.direction == "in":
        receipt = email_envelopes.receipt_for_outreach(db, row.id)
        mid = (
            (env.rfc_message_id if env else "")
            or message_id(receipt.message_id if receipt else "")
            or message_id(row.provider_message_id)
        )
        refs = (env.references if env else "") or ""
        up = (env.in_reply_to if env else "") or (receipt.in_reply_to if receipt else "") or (
            row.in_reply_to or ""
        )
        return _parent(mid, refs, up)
    if env is None or not env.rfc_message_id:
        return Thread()
    return _parent(env.rfc_message_id, env.references or "", env.in_reply_to or "")


def thread_under_receipt(db: Session, receipt: InboundEmail | None) -> Thread:
    """How to answer a delivery, placed or not -- the stranger's mail included."""
    if receipt is None:
        return Thread()
    env = email_envelopes.for_receipt(db, receipt.id)
    mid = (env.rfc_message_id if env else "") or message_id(receipt.message_id)
    refs = (env.references if env else "") or ""
    up = (env.in_reply_to if env else "") or receipt.in_reply_to or ""
    return _parent(mid, refs, up)


# -------------------------------------------------------------------- build


@dataclass
class Message:
    """One email, checked and ready to record. Nothing about it is stored yet."""

    to: list[Recipient]
    cc: list[Recipient]
    bcc: list[Recipient]
    subject: str
    #: The text/plain half, signature included. What `outreach.body` stores.
    text: str
    #: The cleaned HTML, signature block included, or "" for a text-only
    #: message -- the sender then derives paragraphs from `text`.
    html: str = ""
    #: The signature image, HTML only. Built here from a token we minted,
    #: never taken from a request.
    html_tail: str = ""
    importance: str = "normal"
    files: list[EmailAttachment] = field(default_factory=list)
    thread: Thread = field(default_factory=Thread)
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def recipients(self) -> list[Recipient]:
        return [*self.to, *self.cc, *self.bcc]

    @property
    def primary(self) -> str:
        """The first To, bare. `outreach.to_address` holds this and nothing
        else: the gate, the matcher and SMS all read that column as one
        address."""
        return self.to[0].address if self.to else ""


def recipients(values, role: str) -> list[Recipient]:  # noqa: ANN001
    """One role's recipients, or an `OutboundError` naming what was unreadable.

    `values` is what a caller sent -- a string (possibly several addresses),
    a list of strings or `{name, address}` objects -- or `Recipient`s this
    system already parsed off a received message, which are taken as they
    are: that mail was not typed into a form, and refusing to answer a
    sender because their address fails a stricter check than the one that
    delivered it would be a reply lost for a technicality.
    """
    if values is None:
        return []
    items = [values] if isinstance(values, (str, Recipient, dict)) else list(values)
    trusted = [v for v in items if isinstance(v, Recipient)]
    typed = [v for v in items if not isinstance(v, Recipient)]
    found, bad = email_addresses.parse(typed) if typed else ([], [])
    if bad:
        shown = ", ".join(f"'{b}'" for b in bad[:5]) + (" and more" if len(bad) > 5 else "")
        raise OutboundError(
            f"{role}: {shown} {'is not an email address' if len(bad) == 1 else 'are not email addresses'}."
        )
    seen: set[str] = set()
    out: list[Recipient] = []
    for r in [*trusted, *found]:
        if r.key not in seen:
            seen.add(r.key)
            out.append(r)
    return out


def typed_or_on_file(typed, on_file: str | None):  # noqa: ANN001, ANN201
    """What was typed into To, or the buyer's address on file when nothing was.

    `None` when there is neither, which a caller answers with its own 409.
    The lead and appointment composers sent no `to` at all and meant "the
    buyer", and an empty field from a newer composer means the same thing.
    """
    if isinstance(typed, str):
        return typed if typed.strip() else (on_file or None)
    if typed:
        return typed
    return on_file or None


def build(
    db: Session,
    *,
    to,
    cc=None,
    bcc=None,
    subject: str = "",
    body: str = "",
    html: str = "",
    attachment_ids: Sequence[str] | None = None,
    importance: str = "normal",
    thread: Thread | None = None,
    sign: bool = False,
    signer=None,
    base_url: str = "",
    uploader_id: str | None = None,
    headers: dict[str, str] | None = None,
) -> Message:
    """Check everything that can be refused, and return the message.

    `html`, when there is any, is the rep's formatted body: it is cleaned to
    the composer's allowlist and the text half is written from it, so the two
    say the same thing. Without it the text is `body` as typed and the sender
    derives paragraphs from it.

    `sign` appends a sign-off -- `signer`'s own (a rep) or the dealership's
    (`None`, which is what Liner signs with) -- to the text through the same
    idempotent `with_signature` as always, and to the HTML as paragraphs
    beside it, so a formatted message is not the one that goes out unsigned.
    The image a rep may have uploaded rides the HTML only.
    """
    to_list, cc_list, bcc_list = email_addresses.distinct(
        recipients(to, "To"), recipients(cc, "Cc"), recipients(bcc, "Bcc"),
    )
    if not to_list:
        raise OutboundError("A recipient address is required.")
    total = len(to_list) + len(cc_list) + len(bcc_list)
    if total > MAX_RECIPIENTS:
        raise OutboundError(
            f"One message can go to at most {MAX_RECIPIENTS} people, and this one has "
            f"{total}. Split it into more than one."
        )

    subject = header_value(subject)
    cleaned = email_html.clean_outbound(html) if (html or "").strip() else ""
    text = email_html.text_from_html(cleaned) if cleaned else (body or "")
    files = _files(db, attachment_ids, uploader_id)
    # A file is a message, and an attachment-only email is a real thing to
    # send; nothing at all is not.
    if not subject and not text.strip() and not files:
        raise OutboundError("An empty email is not worth sending.")

    tail = ""
    if sign:
        signed = outreach_send.with_signature(db, text, user=signer)
        if cleaned and signed != text.rstrip():
            cleaned += email_html.text_to_html(outreach_send.signature_for(db, signer))
        text = signed
        if signer is not None:
            tail = outreach_send.signature_html(db, signer, base_url)

    level = "high" if (importance or "").strip().lower() == "high" else "normal"
    extra: dict[str, str] = {}
    if level == "high":
        # Both: `Importance` is the standard one (RFC 2156) and Outlook's
        # own, `X-Priority` is what most other clients read.
        extra.update({"Importance": "high", "X-Priority": "1"})
    extra.update(headers or {})

    return Message(
        to=to_list, cc=cc_list, bcc=bcc_list, subject=subject, text=text,
        html=cleaned, html_tail=tail, importance=level, files=files,
        thread=thread or Thread(), headers=extra,
    )


def _files(db: Session, ids: Sequence[str] | None, uploader_id: str | None) -> list[EmailAttachment]:
    """The named files, checked for sending, or an `OutboundError` saying why not."""
    wanted = [i for i in (ids or []) if i]
    if not wanted:
        return []
    try:
        rows = email_envelopes.check_sendable(db, wanted, user_id=uploader_id)
    except AttachmentError as exc:
        raise OutboundError(str(exc)) from None
    _refuse_ours(db, rows)
    return rows


def _refuse_ours(db: Session, rows: Sequence[EmailAttachment]) -> None:
    """A file on mail addressed to *us* is not a dealership's to forward.

    `support@`, `founder@` and `cto@` are Liner's boxes; the dealership's
    mailbox does not list their unplaced mail, and forwarding is not a way
    round that. Answered as "not here" rather than "not yours", the same way
    the list answers: which of our boxes a stranger wrote to is not a fact
    for a rep.
    """
    from app.email_intake import is_ours

    envelope_ids = {a.envelope_id for a in rows if a.envelope_id}
    if not envelope_ids:
        return
    receipt_ids = [
        rid for rid in db.scalars(
            select(EmailEnvelope.receipt_id).where(
                EmailEnvelope.id.in_(envelope_ids), EmailEnvelope.receipt_id.is_not(None)
            )
        ) if rid
    ]
    if not receipt_ids:
        return
    for to_address in db.scalars(select(InboundEmail.to_address).where(InboundEmail.id.in_(receipt_ids))):
        if is_ours(to_address or ""):
            raise OutboundError(
                "An attached file is no longer here -- it may have been removed. "
                "Attach it again and send."
            )


# --------------------------------------------------------------------- send


@dataclass
class Sent:
    """What happened to one message, and the rows that say so."""

    record: Outreach
    envelope: EmailEnvelope | None
    files: list[EmailAttachment]
    sender: EmailSender
    message: Message
    #: The `OUTBOUND_ONLY_TO` refusal, when that is what stopped it.
    blocked: str = ""
    #: What the provider said, when it was reached at all.
    result: SendResult | None = None

    @property
    def ok(self) -> bool:
        return self.record.status == "sent"

    @property
    def detail(self) -> str:
        """The one line a person needs: the refusal, the provider's words, or
        the error the send raised."""
        if self.blocked:
            return self.blocked
        if self.result is not None:
            return self.result.detail or self.record.error or ""
        return self.record.error or ""

    def summary(self) -> dict:
        # A rep sent it, so a rep may see its Bcc.
        return email_envelopes.summary(self.envelope, self.files, include_bcc=True)

    def out(self) -> dict:
        from app.schemas.serialize import outreach_out

        return outreach_out(self.record, email=self.summary())


def send(
    db: Session,
    message: Message,
    *,
    kind: str,
    lead_id: str | None = None,
    appointment_id: str | None = None,
    sent_by_user_id: str | None = None,
    prepare: Callable[[Outreach, Message], None] | None = None,
    announce_event: bool = True,
    conversation_id: str | None = None,
    event: dict | None = None,
) -> Sent:
    """Record the message, then send it, then record what happened.

    `prepare(record, message)` runs once the row has an id and before it is
    committed -- the credit-application link rewrite needs the row's own
    token, and changes the text and the HTML the same way. `sent_by_user_id`
    is `None` for Liner: that NULL is the whole author test the email agent's
    cooldown and the queues read.

    The event is emitted when the provider was reached, whatever it answered,
    exactly as before; a caller that has more to write first -- the
    appointment path mirrors the email into the buyer's chat -- passes
    `announce_event=False` and calls `announce` itself.
    """
    sender = get_email_sender()
    record = Outreach(
        appointment_id=appointment_id,
        lead_id=lead_id,
        sent_by_user_id=sent_by_user_id,
        channel="email",
        direction="out",
        kind=kind,
        to_address=message.primary,
        subject=message.subject,
        body=message.text,
        provider=sender.name,
        status="queued",
        reply_token=outreach_send.mint_reply_token(db),
        in_reply_to=message.thread.in_reply_to or None,
    )
    db.add(record)
    db.flush()
    if prepare is not None:
        prepare(record, message)
        record.body = message.text

    # Reply-To routes back to this row, not to the person who pressed send: a
    # reply has to reach the system to land on the buyer's timeline.
    reply_to = outreach_send.reply_to_address(record.reply_token)
    from_error = ""
    try:
        # Signed with the dealership's own name, read from the row, and
        # stored as the provider will be handed it -- what the envelope says
        # it was from has to be what it was from.
        from_address = outreach_send.dealership_from(db, sender)
        shown_from = sender.from_header(from_address)
    except Exception as exc:  # noqa: BLE001 -- a sender too broken to name its From
        from_address, shown_from, from_error = "", "", str(exc) or type(exc).__name__
    from_name, from_bare = parseaddr(shown_from or "")

    envelope = EmailEnvelope(
        outreach_id=record.id,
        from_name=from_name,
        from_address=from_bare,
        to_json=email_addresses.dumps(message.to),
        cc_json=email_addresses.dumps(message.cc),
        bcc_json=email_addresses.dumps(message.bcc),
        reply_to_json=email_addresses.dumps([Recipient("", reply_to)] if reply_to else []),
        # As sent: the cleaned body, its sign-off and the image under it. A
        # text-only message stores none and the reader shows its text.
        html=(message.html + message.html_tail) if message.html else "",
        importance=message.importance,
        in_reply_to=message.thread.in_reply_to,
        references=message.thread.references,
        size=len(message.text.encode()) + len(message.html.encode())
        + sum(a.size or 0 for a in message.files),
    )
    db.add(envelope)
    db.flush()
    try:
        files = (
            email_envelopes.claim(db, [a.id for a in message.files], envelope, user_id=sent_by_user_id)
            if message.files else []
        )
    except AttachmentError as exc:
        # Nothing is committed yet, so a refusal here leaves no half-made row
        # behind -- the same answer `build` would have given a moment earlier.
        db.rollback()
        raise OutboundError(str(exc)) from None
    _unreferenced_inline(files, message.html)
    # **The row exists before the provider is asked.** Whatever the send does
    # next -- refuse, time out, raise -- there is a row to say so.
    db.commit()

    sent = Sent(record=record, envelope=envelope, files=files, sender=sender, message=message)

    # One guard, shared with every other send; see app/outreach_send.py. Every
    # recipient, Cc and Bcc included.
    blocked = outreach_send.blocked_reason(sender, [r.address for r in message.recipients])
    if blocked:
        _failed(db, record, blocked)
        sent.blocked = blocked
        return sent
    if from_error:
        _failed(db, record, from_error)
        return sent

    try:
        outgoing = email_envelopes.outgoing(files)
        result = sender.send(
            [display(r) for r in message.to],
            message.subject,
            message.text,
            reply_to=reply_to,
            in_reply_to=message.thread.in_reply_to,
            from_address=from_address,
            html_tail=message.html_tail,
            cc=[display(r) for r in message.cc] or None,
            bcc=[display(r) for r in message.bcc] or None,
            html=message.html,
            attachments=outgoing or None,
            references=message.thread.references,
            headers=message.headers or None,
            idempotency_key=record.id,
        )
    except Exception as exc:  # noqa: BLE001 -- NotConfigured, a vendor error, a missing file
        _failed(db, record, str(exc) or type(exc).__name__)
        return sent

    record.provider_message_id = result.message_id
    record.provider_thread_id = result.thread_id
    # 'sent' means the provider accepted it. There is no delivery callback.
    record.status = result.status
    record.error = result.detail if result.status != "sent" else ""
    record.sent_at = utcnow()
    envelope.rfc_message_id = message_id(result.rfc_message_id)
    envelope.dated_at = record.sent_at
    db.commit()
    sent.result = result
    if announce_event:
        announce(db, sent, conversation_id=conversation_id, **(event or {}))
    return sent


def display(r: Recipient) -> str:
    """`Name <address>` as a sender is handed it: quoted, never encoded.

    `Recipient.header()` is `formataddr`, which writes a non-ASCII name as an
    RFC 2047 encoded word -- right inside a MIME message, and the wrong thing
    to hand a JSON API that builds its own headers, where `=?utf-8?b?...?=`
    may arrive as the name itself. Each sender encodes for its own wire:
    Gmail's `EmailMessage` does it on assignment, Resend does it on its side.
    """
    name = header_value(r.name)
    if not name:
        return r.address
    if name.isascii():
        return formataddr((name, r.address))
    quoted = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{quoted}" <{r.address}>'


def announce(db: Session, sent: Sent, *, conversation_id: str | None = None, **extra) -> None:
    """`outreach.sent`, with what the message carried.

    `to` stays the one address every listener already reads; the counts are
    new keys, so a dashboard that knows nothing of Cc keeps working.
    """
    record = sent.record
    emit(db, "outreach.sent", {
        "outreach_id": record.id,
        "appointment_id": record.appointment_id,
        "lead_id": record.lead_id,
        "to": record.to_address,
        "cc_count": len(sent.message.cc),
        "attachments": len(sent.files),
        "provider": record.provider,
        "delivered_externally": sent.sender.delivers,
        "conversation_id": conversation_id,
        **extra,
    })


def _failed(db: Session, record: Outreach, why: str) -> None:
    record.status = "failed"
    record.error = why
    db.commit()


def _unreferenced_inline(files: Sequence[EmailAttachment], html: str) -> None:
    """An inline image nothing in this message's HTML shows is a file.

    Forwarding a message copies its files, and the logo its signature carried
    inline comes along as an inline part -- but the composer's HTML cannot
    hold an image, so nothing refers to it. Sent inline it is invisible in
    some clients, and listed nowhere on our side either (`summary` leaves
    inline images out, as part of the body). So it goes as what it now is.
    """
    for a in files:
        if a.disposition == "inline" and a.content_id and f"cid:{a.content_id}" not in (html or ""):
            a.disposition = "attachment"
            a.content_id = ""


def rewrite(message: Message, target: str, replacement: str) -> None:
    """Replace one string in both halves of a message the same way.

    For a link: the text half carries it as typed, the HTML half carries it
    escaped inside an attribute and as link text, so both spellings go.
    """
    if not target:
        return
    message.text = message.text.replace(target, replacement)
    if message.html:
        message.html = message.html.replace(target, replacement).replace(
            _html.escape(target, quote=True), _html.escape(replacement, quote=True)
        )
