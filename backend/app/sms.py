"""Texting a buyer, and reading what they text back.

**No assistant touches this, and there is deliberately no switch that would let
one.** Every message here is written by a person and sent because they pressed
send. That is not a phase-one compromise: an autonomous texter needs its own
consent story, its own rate limit and its own loop-breaker, and none of those
exist yet. The assistant's own `OPERATING_RULES` still says it cannot text,
which stays true and stays there.

**It reuses `Outreach`, which is not a shortcut.** An inbound email is already
stored as an `Outreach` row with `direction="in"`, and `app/timeline.py` folds
both directions into the buyer's history with no entry kind of its own. A text
is the same shape -- a number, a body, a provider id, a status -- so it gets
`channel="sms"` and everything downstream works: the buyer page, the channel
strip, the recap. A table of its own would have bought a second timeline
composer and a second definition of what counts as contact.

Three brakes, and two of them are the ones email already has:

* `OUTBOUND_ONLY_TO` gates it exactly as it gates mail. A rehearsal that texts
  a real prospect is worse than one that mails them -- it costs money per
  message and arrives on a phone at whatever hour it is there.
* **STOP is honoured here as well as at Twilio.** Twilio blocks a number that
  texted STOP and answers a send to it with error 21610, so the law is met
  either way -- but a system that keeps trying learns nothing, logs a failure
  per attempt, and shows a rep a send that looks like it went. The opt-out is
  recorded on our side and the send is refused before it is attempted.
* An unconfigured account refuses rather than pretending, like everything else
  here.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.db import ops_session, utcnow
from app.events import emit, emit_ops
from app.integrations import twilio_account as account
from app.integrations.sms import twilio_sms
from app.matching import digits, match_lead
from app.models import Lead, Outreach, SmsOptOut, User

log = logging.getLogger("liner.sms")

#: What a person texts to stop hearing from us. Twilio recognises these at the
#: account level and blocks the number itself; we recognise them too so the
#: refusal happens here, before a send that would come back 21610.
STOP_WORDS = frozenset({
    "stop", "stopall", "unsubscribe", "cancel", "end", "quit", "stop all",
})

#: And to start again. `start` and `unstop` are Twilio's; `yes` is in their
#: list too, which is worth knowing -- somebody answering "yes" to a question
#: has also, as far as the carrier is concerned, resumed.
START_WORDS = frozenset({"start", "unstop", "yes"})

#: Twilio's code for "this recipient has opted out of messages from this
#: number". Recognised so a send refused for that reason records the opt-out we
#: did not already know about, rather than failing the same way forever.
OPTED_OUT_CODE = "21610"


# --------------------------------------------------------------------------
# Opting out
# --------------------------------------------------------------------------

def opted_out(phone: str) -> bool:
    """Has this number told us to stop?

    **No `db` argument, on purpose.** `ops_sms_opt_outs` is in Liner's own
    database now, not a dealership's -- there is one Twilio number and an
    opt-out is against *it*, so it cannot be per store. Taking a session would
    mean four callers each deciding which one to hand over, and the one that
    guessed the dealership's would get "not opted out" for somebody who had.
    That answer is a text to a person who said stop.
    """
    with ops_session() as ops:
        row = _opt_out_row(ops, phone)
        return row is not None and row.resumed_at is None


def _opt_out_row(db: Session, phone: str) -> SmsOptOut | None:
    """`db` here is **Liner's own**, not the dealership's -- see `opted_out`."""
    key = digits(phone)
    if not key:
        return None
    return db.query(SmsOptOut).filter_by(phone_key=key).one_or_none()


def opt_out(phone: str, reason: str = "STOP") -> SmsOptOut | None:
    """Record that this number wants nothing more. Idempotent.

    Written to Liner's own database, and the event goes there too: the number
    is ours and the consent is against it, so neither belongs in a store.
    """
    key = digits(phone)
    if not key:
        return None
    with ops_session() as ops:
        row = _opt_out_row(ops, phone)
        if row is None:
            row = SmsOptOut(phone_key=key, phone=phone, reason=reason)
            ops.add(row)
        else:
            row.reason = reason
            row.at = utcnow()
        # A second STOP after a START is a fresh opt-out, not a no-op.
        row.resumed_at = None
        ops.commit()
        log.info("sms opt-out recorded for %s (%s)", key, reason)
        emit_ops("sms.opt_out", {"phone": phone, "reason": reason})
        ops.expunge(row)
        return row


def resume(phone: str, reason: str = "START") -> SmsOptOut | None:
    """They texted START. Kept as a row rather than deleted: the opt-out
    happened, and a consent record that erases its own history answers nothing
    when somebody asks about it later."""
    with ops_session() as ops:
        row = _opt_out_row(ops, phone)
        if row is None:
            return None
        row.resumed_at = utcnow()
        ops.commit()
        emit_ops("sms.resumed", {"phone": phone, "reason": reason})
        ops.expunge(row)
        return row


def keyword(body: str) -> str:
    """"stop", "start", or "" -- what a carrier would read this message as.

    Matched on the whole trimmed body, never on a word inside it: "please stop
    sending me the blue one" is a sentence about a car, and unsubscribing
    somebody who was mid-conversation is worse than missing a keyword they
    would have sent on its own line anyway.
    """
    text = " ".join((body or "").strip().lower().split())
    if text in STOP_WORDS:
        return "stop"
    if text in START_WORDS:
        return "start"
    return ""


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

def blocked_reason(to: str) -> str:
    """Why this text must not go out, or "" if it may.

    Both gates in one place, so a caller cannot check one and forget the
    other -- the same reason `outreach_send.blocked_reason` is one function.
    """
    if opted_out(to):
        return (
            f"Not sent: {to} texted STOP and has not texted START. "
            "Nothing further goes to that number until they do."
        )
    allowed = settings.outbound_recipients
    if allowed is None:
        return ""
    # Compared on the last ten digits as well as verbatim: OUTBOUND_ONLY_TO is
    # written by a person, and "+15025550142" and "(502) 555-0142" are one
    # number. An address in the list simply will not match a phone, which is
    # correct -- one setting, and each entry only ever allows what it names.
    key = digits(to)
    if (to or "").lower() in allowed or (key and any(digits(a) == key for a in allowed)):
        return ""
    return (
        f"Not sent: OUTBOUND_ONLY_TO does not include {to}. "
        + (
            f"It currently allows {', '.join(allowed)}. "
            if allowed else "It is empty, so every recipient is refused. "
        )
        + "Add the number to it, or set OUTBOUND_ONLY_TO=everyone to send freely."
    )


def send(
    db: Session,
    to: str,
    body: str,
    *,
    lead: Lead | None = None,
    by: User | None = None,
    status_url: str = "",
) -> Outreach:
    """Text somebody, and keep the row whatever happens.

    The row is written *before* the attempt and updated after, so a send that
    the provider refused is still on the buyer's timeline with the reason on
    it. Dropping it would lose the one message a rep most needs to find again
    -- the same rule `ops_messages` follows for a failed email.
    """
    row = Outreach(
        lead_id=lead.id if lead is not None else None,
        sent_by_user_id=by.id if by is not None else None,
        channel="sms",
        direction="out",
        kind="followup",
        to_address=to,
        body=body,
        provider="twilio",
        status="queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    refusal = blocked_reason(to)
    if refusal:
        row.status = "failed"
        row.error = refusal
        db.commit()
        return row

    try:
        result = twilio_sms.send(to, body, status_url)
    except Exception as exc:  # NotConfigured, and anything the vendor raised
        detail = str(exc)
        row.status = "failed"
        row.error = detail
        db.commit()
        # Twilio telling us they have opted out is the one error worth acting
        # on rather than only recording: without this the next send fails the
        # same way, and the one after that.
        if OPTED_OUT_CODE in detail:
            opt_out(to, reason=f"Twilio {OPTED_OUT_CODE}")
        return row

    row.provider_message_id = str(result.get("sid") or "")
    # Twilio's own word, verbatim: queued, sending, sent, delivered, failed,
    # undelivered. Not mapped onto ours -- "undelivered" and "failed" are
    # different facts and only one of them is worth trying again.
    row.status = str(result.get("status") or "sent")
    row.sent_at = utcnow()
    db.commit()
    emit(db, "sms.sent", {
        "outreach_id": row.id, "lead_id": row.lead_id, "to": to,
    })
    return row


# --------------------------------------------------------------------------
# Receiving
# --------------------------------------------------------------------------

def receive(db: Session, params: dict[str, str]) -> Outreach | None:
    """One inbound text, filed. Returns the row, or None if it was a repeat.

    Deduped on Twilio's `MessageSid`: a webhook that times out on our side is
    retried, and a buyer's question landing twice on their timeline is a rep
    answering something they already answered.

    Resolution is the one matcher, by number -- `match_lead` with no email, so
    it falls to the phone rung. A name is never part of it here any more than
    it is in email: two people share a household number, and attaching a
    stranger to whoever happens to have it is the failure `app/matching.py`
    exists to prevent. A text that matches nobody is **stored anyway**, with no
    lead, exactly as an unplaceable email is.
    """
    sid = (params.get("MessageSid") or params.get("SmsSid") or "").strip()
    from_number = (params.get("From") or "").strip()
    body = params.get("Body") or ""

    if sid:
        already = (
            db.query(Outreach)
            .filter_by(provider_message_id=sid, channel="sms", direction="in")
            .first()
        )
        if already is not None:
            return None

    # Before anything else is decided. Somebody who texted STOP has said the
    # one thing that must take effect even if every other step fails.
    word = keyword(body)
    if word == "stop":
        opt_out(from_number, reason=(body or "STOP").strip()[:40])
    elif word == "start":
        resume(from_number)

    lead = match_lead(db, "", from_number)
    row = Outreach(
        lead_id=lead.id if lead is not None else None,
        channel="sms",
        direction="in",
        kind="followup",
        to_address=from_number,
        body=body,
        provider="twilio",
        provider_message_id=sid or None,
        status="received",
        sent_at=utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    emit(db, "sms.received", {
        "outreach_id": row.id,
        "lead_id": row.lead_id,
        "from": from_number,
        # So a dashboard can tell "a buyer wrote" from "a stranger did" without
        # a second request, the same way the mailbox does.
        "resolved": lead is not None,
    })
    return row


def claim_unresolved(db: Session, lead: Lead) -> int:
    """Texts this buyer sent before we knew who they were.

    The other half of the ladder, and it exists for the reason the email one
    does: resolution runs once, when the message arrives, so somebody who texts
    the number before they are anyone here is stored unresolved -- correctly --
    and would stay a stranger forever once they booked. `attach_lead` calls
    this at the moment a buyer comes into existence.

    By number only. A text carries no address and no name, so the phone rung is
    the whole ladder here.
    """
    key = digits(lead.phone or "")
    if not key:
        return 0
    claimed = 0
    for row in (
        db.query(Outreach)
        .filter(Outreach.channel == "sms", Outreach.lead_id.is_(None))
        .all()
    ):
        if digits(row.to_address or "") == key:
            row.lead_id = lead.id
            claimed += 1
    if claimed:
        db.commit()
        log.info("claimed %d unplaced text(s) onto lead %s", claimed, lead.id)
    return claimed


def unresolved(db: Session, limit: int = 50) -> list[Outreach]:
    """Texts from numbers that match nobody. Listed on `/ops/phone`, because
    the number they arrived at is Liner's -- there is one line, so unlike an
    email there is no addressee to say whose desk it belongs on."""
    return (
        db.query(Outreach)
        .filter(
            Outreach.channel == "sms",
            Outreach.direction == "in",
            Outreach.lead_id.is_(None),
        )
        .order_by(Outreach.created_at.desc())
        .limit(limit)
        .all()
    )


def configured() -> bool:
    """SMS needs exactly what the phone line needs -- one account, one number."""
    return account.configured()
