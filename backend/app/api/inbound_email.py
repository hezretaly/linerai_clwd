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
from email.utils import parseaddr
from hashlib import sha256

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import matching
from app.config import settings
from app.api.deps import current_user
from app.db import get_db, utcnow
from app.escalations import owner_of
from app.events import emit
from app.email_intake import (
    as_text,
    automated_reason,
    display_name,
    just_the_reply,
    sender_address,
    signature_name,
)
from app.schemas.serialize import iso
from app.models import (
    Appointment,
    CapturedField,
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
# Matches the Worker's `express.json({ limit: "10mb" })`.
MAX_BODY = 10 * 1024 * 1024

REPLY_RE = re.compile(r"reply\+([A-Za-z0-9_-]{6,64})@", re.IGNORECASE)

# Prefix on a message id this app invented because the mail carried none.
# It has to be recognisable: a synthetic id is a dedupe key and nothing
# else, and must never be echoed back out as an In-Reply-To header naming
# a message that does not exist.
SYNTHETIC = "sha256:"


class InboundBody(BaseModel):
    """What a Worker posts. Every field is optional *and nullable*.

    `str = ""` was wrong, and wrong in the way that costs real mail. A Worker
    writes `inReplyTo: parsed.inReplyTo ?? null` because that is the obvious
    way to say "there wasn't one", and pydantic rejected the null as a type
    error -- so every reply that was not itself threaded came back 400
    malformed. Worse, a sensible Worker treats 4xx as "my payload is wrong,
    retrying will not help" and gives up, which turns a schema quibble into a
    buyer's reply that is gone for good.

    Nothing downstream reads these attributes anyway: every value is pulled
    out of `model_dump()` and coerced with `or ""`. The declarations are
    documentation of the shape, so they must not be stricter than the wire.
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
    background: BackgroundTasks,
    x_liner_signature: str = Header(default=""),
    x_webhook_secret: str = Header(default=""),
    db: Session = Depends(get_db),
) -> dict:
    raw = await request.body()

    # An intake no session guards is an intake anyone can point a firehose at.
    # 10 MB is the documented ceiling on the Worker side; matching it here
    # means the refusal happens before the body is parsed.
    if len(raw) > MAX_BODY:
        _receipt(db, outcome="malformed",
                 detail=f"Body was {len(raw)} bytes; the limit is {MAX_BODY}.")
        raise HTTPException(413, "Payload too large")

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
    sender = (data.get("from") or data.get("from_") or "").strip()
    to = (data.get("to") or "").strip()
    subject = (data.get("subject") or "").strip()
    # Plain text when there is any; otherwise the HTML part with its tags
    # taken off, which beats storing markup in a field rendered as text.
    body = (data.get("text") or "").strip() or as_text(data.get("html") or "")
    in_reply_to = (data.get("inReplyTo") or "").strip()
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

    envelope = {
        "message_id": message_id, "from_address": sender, "to_address": to,
        "subject": subject, "body": body, "in_reply_to": in_reply_to,
    }

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

    # Claim the message before answering, then do the work after. Returning
    # 200 fast matters: the Worker rejects the message to the sender on a
    # non-2xx, so a slow CRM bounces a real buyer's reply.
    #
    # The claim is why this is written down rather than just backgrounded. A
    # plain "return 200, process later" loses the dedupe it is sitting right
    # next to -- a retry arriving mid-processing finds no accepted receipt and
    # files the reply twice. The row goes in first, holding the message id;
    # the background pass fills in what it resolved to.
    claim = _receipt(db, outcome="received", detail=f"Authenticated by {proved_by}.",
                     **envelope)
    # Decided here, not in the background pass, because this is where the
    # headers still exist -- `extra="allow"` keeps whatever the Worker sent,
    # and a header is a sender declaring itself a machine, which is the only
    # loop-breaker that stops a vacation responder on its first turn. It is
    # handed over as an argument rather than stored: `create_all` adds a table
    # to a database that already exists and never a column.
    background.add_task(
        _place, claim.id, automated_reason(sender, data.get("headers"), body)
    )
    return {"ok": True, "outcome": "received", "receipt_id": claim.id}


def _place(receipt_id: str, refused: str = "") -> None:
    """Resolve one claimed delivery and store it. Runs after the response.

    Its own session: the request's is closed by the time this runs, and
    reusing it is the classic background-task crash.

    `refused` is `automated_reason`'s verdict, decided back in the request
    where the headers still exist and handed over as an argument. It is not a
    column because it cannot be one: `create_all` adds a table to a database
    that already exists and never a column, and there is no Alembic here by
    design. It is empty when a lead already exists, since the question only
    arises for a delivery that matched nobody.
    """
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        claim = db.query(InboundEmail).filter_by(id=receipt_id).one_or_none()
        if claim is None or claim.outcome != "received":
            return

        lead, outreach, matched_by = _resolve(
            db, claim.to_address, claim.in_reply_to, claim.from_address
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
            claim.detail = (
                f"Not filed against anyone: {why_not} Kept rather than dropped -- "
                "something really arrived, and a delivery nobody can place is the "
                "only way to tell a filtered sender from a broken mail route."
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
            })
            return

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
            sent_at=utcnow(),
        )
        db.add(record)
        db.flush()

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
            "outcome": "accepted", "reopened": reopened,
        })
    except Exception as exc:  # never let a background failure vanish
        claim = db.query(InboundEmail).filter_by(id=receipt_id).one_or_none()
        if claim is not None:
            claim.outcome = "failed"
            claim.detail = f"Could not be filed: {exc}"[:500]
            db.commit()
    finally:
        db.close()


def _is_ours(recipient: str) -> str:
    """Was this addressed to Liner rather than to the dealership?

    Read from the settings that already name our two published addresses, plus
    `cto@` on the same domain, rather than a second hardcoded list -- the
    landing page and the ops mailbox both read the same values, and a third
    copy is how one of them starts disagreeing about who owns an inbox.

    `reply+<token>@` is never ours whatever the domain: it is minted by a send
    to a buyer and routes back into their timeline.
    """
    address = sender_address(recipient) or (recipient or "").strip().lower()
    if not address or REPLY_RE.search(recipient or ""):
        return ""
    # Compared on the **local part**, not the whole address. The Worker's own
    # recipient filter does the same, and for the same reason: mail reaches
    # these boxes through whatever domain Cloudflare is routing, and a dealer
    # forwarding from a second one is normal. Matching the full address meant
    # `support@` on any other domain read as the dealership's.
    ours = {
        (settings.support_email or "").partition("@")[0].strip().lower(),
        (settings.founder_email or "").partition("@")[0].strip().lower(),
        "cto",
    }
    local = address.partition("@")[0]
    return address if local in {a for a in ours if a} else ""


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

    The name is the envelope's display name, which is a fact the sender's own
    client asserts, never the signature -- that is a guess, and it is recorded
    as one below. Neither is ever used to *match*: `app/matching.py` stays
    email exact and phone by its last ten digits, because a name is not
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
    if _is_ours(claim.to_address):
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

    lead = Lead(
        name=display_name(claim.from_address),
        email=address,
        phone="",
        source="email",
    )
    db.add(lead)
    db.flush()

    # People sign their mail, and it is the only name an envelope with no
    # display name offers. Recorded as a captured field with provenance
    # `inferred` rather than written onto the lead: prose cannot carry
    # provenance, and a guessed name asserted as fact is one a rep repeats on
    # the phone to somebody it does not belong to. A rep confirms it or
    # replaces it, and until they do the row reads as unnamed, which is true.
    signed = signature_name(just_the_reply(claim.body))
    if signed and signed != lead.name:
        db.add(CapturedField(
            lead_id=lead.id, key="signed_name", value=signed, provenance="inferred",
        ))
    db.commit()

    # The other half of the ladder, and the reason it is called here rather
    # than left for later: they may have written three times before this one.
    matching.claim_unresolved(db, lead)
    emit(db, "lead.created", {"lead_id": lead.id, "source": "email"})
    return lead, ""


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
    lead = matching.match_lead(db, sender_address(sender), "")
    if lead is not None:
        return lead, None, "from_address"

    return None, None, ""


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
