"""Liner answering one email, once every brake has said it may.

Every guard the chat loop runs, runs here: `run_turn` is the same loop, the
same eight tools and the same reply guards, given an addendum about being in an
inbox rather than on a screen. A second copy of the loop is how one channel
quietly stops running the guards, which is the argument `agent/loop.py` already
makes about vendors.

**The conversation is minted on the first reply, not on the third exchange.**
`Conversation` is what carries Take over, `agent_paused`, escalation and the
message rows; without one, for the first two exchanges a rep could not grab the
thread and the kill switch would be the only brake -- on the turns where Liner
is guessing most. What the three-exchange threshold governs is *presentation*:
below it the row lives in the inbound list, at it the buyer appears in the
conversations list. See `app/email_threads.py`.

**The thread comes from our rows, never from the quoted block.** A quote is the
buyer's own mail client's copy of what we sent, and it can be truncated, edited
or machine-translated on the way back; anything in it arrives looking like
something we said. `just_the_reply` throws that mirror away and the history
comes from `messages`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app import email_agent, email_envelopes, email_outbound, outreach_status
from app.config import settings
from datetime import timedelta

from app.db import utcnow
from app.email_intake import just_the_reply
from app.events import emit
from app.models import (
    Conversation, EmailReplyDue, InboundEmail, Lead, Message, Outreach, User,
)

#: How much of one message reaches the model. Long enough for anything a person
#: types and short enough that a forwarded forty-page thread cannot quietly
#: become the prompt.
#:
#: Over it, Liner does not answer badly -- it hands the message to a person.
#: That is the same rule everything else here follows: report unavailable
#: rather than simulate. Truncating and answering anyway would mean confidently
#: replying to the top of something whose actual question was at the bottom.
MAX_BODY_CHARS = 4000


def readable(body: str) -> tuple[str, str]:
    """What the model may see, and why it may not see it.

    The **top** is kept when trimming for length, because a person's ask is at
    the top and the boilerplate is at the bottom -- but the cap is a refusal
    rather than a trim, so nothing is silently answered on half a message.
    """
    text = just_the_reply(body or "")
    if not text.strip():
        return "", "the message has no readable body"
    if len(text) > MAX_BODY_CHARS:
        return "", (
            f"the message is {len(text)} characters, past the {MAX_BODY_CHARS} "
            "Liner will answer on its own -- a person should read this one"
        )
    return text, ""


def thread_for(db: Session, lead: Lead) -> Conversation:
    """This buyer's email conversation, made on the first reply.

    Reused rather than remade: a buyer with three email threads would have
    three sets of Take over buttons and three places their history could be.
    """
    existing = (
        db.query(Conversation)
        .filter_by(lead_id=lead.id, channel="email")
        .order_by(Conversation.started_at.asc())
        .first()
    )
    if existing is not None:
        return existing
    convo = Conversation(lead_id=lead.id, channel="email", stage="opening")
    db.add(convo)
    db.commit()
    emit(db, "conversation.started", {
        "conversation_id": convo.id, "lead_id": lead.id, "channel": "email",
    })
    return convo


def remember_inbound(db: Session, convo: Conversation, received: Outreach, text: str) -> None:
    """Put the buyer's email into their thread, exactly once.

    Two callers reach this: the intake, which mints the thread on the *first*
    delivery so a rep has a conversation to take over, and `answer`, which
    needs the message in the transcript before the model reads it. Whichever
    arrives first writes it; the other finds it and does nothing. Written twice,
    the buyer's page showed the same email twice -- and it would have been the
    unanswered ones, since those are the deliveries the intake handles alone.

    Always mirrored, never plain. `tool_calls_json` carries the `outreach_id`
    and `app/timeline.py` folds a mirror into the outreach row it copies, so
    the buyer's page shows one entry rather than a message beside an identical
    email. That is the same mechanism an appointment confirmation uses.
    """
    already = (
        db.query(Message)
        .filter(
            Message.conversation_id == convo.id,
            Message.role == "buyer",
            Message.tool_calls_json.contains(received.id),
        )
        .first()
    )
    if already is not None:
        return
    db.add(Message(
        conversation_id=convo.id, role="buyer", content=text,
        tool_calls_json=_dump([{"name": "outreach", "outreach_id": received.id}]),
        # When it arrived, not when it was filed: mail a stranger sent last
        # week and placed today, once they became a buyer, belongs last week
        # in the thread the model reads, not after everything since.
        **({"created_at": received.sent_at} if received.sent_at else {}),
    ))
    db.commit()


def answer(
    db: Session,
    claim: InboundEmail,
    lead: Lead,
    received: Outreach,
    *,
    automated: str = "",
    provider=None,
) -> dict:
    """Compose and send one reply, or say precisely why not.

    Returns a verdict either way rather than raising: a refusal is a thing to
    record on the receipt and read later, and half of them are ordinary -- the
    agent being off is not an error.
    """
    verdict = email_agent.may_reply(
        db, lead, automated=automated, has_provider=provider is not None
    )
    if not verdict.allowed:
        return {"sent": False, "reason": verdict.reason, "detail": verdict.detail}

    text, refused = readable(claim.body)
    if refused:
        _hand_over(db, lead, refused)
        return {"sent": False, "reason": "handed_over", "detail": refused}

    convo = thread_for(db, lead)
    # A rep holding the thread is holding it on every channel. This is the
    # flag Take over sets, and it is checked here rather than only at the
    # start: a rep pressing it while a reply is being composed is exactly the
    # race the double-email rule exists to prevent.
    if convo.agent_paused:
        return {
            "sent": False, "reason": "rep_holding",
            "detail": "A rep has taken this thread over.",
        }

    remember_inbound(db, convo, received, text)

    from app.agent.loop import run_turn
    from app.integrations.base import NotConfigured

    try:
        reply, calls = run_turn(db, convo, text, provider, channel="email")
    except NotConfigured as exc:
        # `enabled()` already refuses when LLM_MODE is not live, so reaching
        # here means live mode with a key the vendor rejected or a setting
        # that changed under us. Reported rather than raised: a traceback
        # coming back up through a background task is indistinguishable from a
        # buyer who never wrote.
        return {"sent": False, "reason": "no_model", "detail": str(exc)}

    # Checked again, immediately before the wire. `may_reply` ran before a
    # model round trip that takes seconds, and a rep who pressed Take over or
    # threw the kill switch during it must not be overtaken by a message that
    # was already in flight.
    #
    # **No Message is written on this path.** Nothing is sent, so there is
    # nothing to mirror -- writing the model's draft into the thread anyway
    # (the earlier version did, before this check) left a Liner reply on the
    # buyer's page that they never received, and `channel_counts`' old
    # per-conversation unit turned that phantom message into an extra "Email"
    # contact on top of the emails actually sent (items 20, 32, 33).
    again = email_agent.may_reply(
        db, lead, automated=automated, has_provider=provider is not None
    )
    db.refresh(convo)
    if not again.allowed or convo.agent_paused:
        return {
            "sent": False,
            "reason": "overtaken" if again.allowed else again.reason,
            "detail": (
                "A person took the thread over while this was being written."
                if again.allowed else again.detail
            ),
        }

    subject = claim.subject or "Your enquiry"
    # Under the message it answers: its Message-ID, then the chain above it --
    # from the receipt, which is the durable record of what arrived, and from
    # the row it was filed as where the receipt has nothing to say.
    thread = email_outbound.thread_under_receipt(db, claim)
    if not thread.in_reply_to:
        thread = email_outbound.thread_under_outreach(db, received)
    try:
        message = email_outbound.build(
            db,
            to=answer_to(db, claim, lead),
            subject=subject if subject.lower().startswith("re:") else f"Re: {subject}",
            body=reply,
            thread=thread,
            # Same sign-off a rep's reply gets, from the same place -- the
            # dealership's, because nobody here wrote it. The model is no
            # longer asked to write one: an improvised sign-off drifts between
            # emails and is a second place the dealership's phone number could
            # be invented, which is what `answer_from_knowledge` exists to
            # prevent everywhere else.
            sign=True, signer=None,
            # RFC 3834: an automatic answer says so, and the buyer's own
            # vacation responder then does not answer it back. The same header
            # `email_intake` refuses to answer is the one we send.
            headers={"Auto-Submitted": "auto-replied"},
        )
    except email_outbound.OutboundError as exc:
        # Same rule as above: no address to answer means nothing left the
        # building, so nothing is written into the thread claiming it did.
        return {"sent": False, "reason": "no_address", "detail": str(exc)}

    sent = email_outbound.send(
        db, message, kind="reply", lead_id=lead.id,
        # NULL, and that is the whole author test -- a rep's send carries their
        # id. The cooldown, the pause and every "did a person answer" question
        # read this rather than a column added for them.
        sent_by_user_id=None,
        conversation_id=convo.id,
        event={"by_liner": True},
    )
    # Mirrored the way `remember_inbound` mirrors the buyer's own half: the
    # `Message` carries `outreach_id`, so `app/timeline.py`'s compose() folds
    # it into the `Outreach` card it copies -- one entry per email, with the
    # row's real delivery status on it -- rather than a separate "message"
    # entry sitting beside an identical-looking outreach card. Written even
    # when the send was blocked or the provider refused it: `sent.record`
    # exists either way (the row is committed before the provider is asked),
    # and a rep needs to see that Liner *tried* to answer and what happened,
    # not nothing at all.
    db.add(Message(
        conversation_id=convo.id, role="liner", content=reply,
        tool_calls_json=_dump([*calls, {"name": "outreach", "outreach_id": sent.record.id}]),
    ))
    db.commit()

    if sent.blocked:
        return {"sent": False, "reason": "blocked", "detail": sent.blocked,
                "outreach_id": sent.record.id}
    if not sent.ok:
        # Refused by the provider or never reached it: either way the row
        # says failed and why, and a send that did not happen is not reported
        # as one that did.
        return {"sent": False, "reason": "provider", "detail": sent.detail,
                "outreach_id": sent.record.id}
    return {"sent": True, "reason": "", "outreach_id": sent.record.id,
            "conversation_id": convo.id, "body": reply}


def answer_to(db: Session, claim: InboundEmail, lead: Lead) -> list:
    """Who Liner's answer goes to: where the message asked, else who wrote it.

    The received message's `Reply-To` when it had one (RFC 5322 section
    3.6.3), because that is the sender saying where answers belong -- a
    buyer writing from a work account with their own address in Reply-To.
    Otherwise the person in `From`, then the address on file. **Never one of
    ours**: a Reply-To pointing at our own mailbox would have Liner writing to
    itself, which is a loop with a cooldown for a brake.

    Parsed leniently, as received mail is: the address delivered, so a
    stricter check than the one that delivered it is not a reason to leave a
    buyer unanswered.
    """
    from app.email_addresses import Recipient, from_header_value, loads

    env = email_envelopes.for_receipt(db, claim.id)
    replying = email_envelopes.without_ours(loads(env.reply_to_json) if env else [])
    if replying:
        return replying
    sender = email_envelopes.without_ours(from_header_value(claim.from_address))
    if sender:
        return sender[:1]
    return email_envelopes.without_ours([Recipient("", lead.email)] if lead.email else [])


def send_rep_reply(db: Session, convo: Conversation, text: str, user: User) -> Message:
    """A rep's reply on an email thread, sent as a real email -- never a bare
    `Message` that only looks like one.

    `api/conversations.py`'s generic `rep_reply` writes a plain `role='rep'`
    `Message` and emits `conversation.message`, which is read live by the
    chat widget's open tab -- there is no such reader for an email thread, so
    that write reached the buyer's own page and nowhere else. A rep replying
    to an email buyer that way read as answered on the buyer page and moved
    `/api/conversations`' last-activity sort, while the Mail page kept them
    `waiting: true` forever, because `email_threads.tally()` -- the one place
    "waiting" is decided -- only ever clears on a real outbound `Outreach`
    row, and this path never wrote one (items 33, 44).

    Build and send a real email under the lead's latest inbound message, and
    only on success mirror it into the thread the way `remember_inbound`
    mirrors the buyer's own half -- `app/timeline.py`'s `compose()` then
    folds it into the one card for that send, the same as any other reply. A
    refusal raises `email_outbound.OutboundError` rather than writing an
    apparently-sent `Message`; the caller turns that into the 4xx/5xx the
    composer reads.
    """
    lead = db.query(Lead).filter_by(id=convo.lead_id).one_or_none() if convo.lead_id else None
    if lead is None or not (lead.email or "").strip():
        raise email_outbound.OutboundError(
            "No email address on file for this buyer -- there is nothing to "
            "reply to by email.", status=409,
        )

    latest_inbound = (
        db.query(Outreach)
        .filter(
            Outreach.lead_id == lead.id, Outreach.channel == "email",
            Outreach.direction == "in",
        )
        .order_by(Outreach.created_at.desc())
        .first()
    )
    thread = email_outbound.thread_under_outreach(db, latest_inbound)
    subject = (latest_inbound.subject if latest_inbound else "") or "Your enquiry"
    message = email_outbound.build(
        db, to=lead.email,
        subject=subject if subject.lower().startswith("re:") else f"Re: {subject}",
        body=text, thread=thread, sign=True, signer=user,
    )
    sent = email_outbound.send(
        db, message, kind="reply", lead_id=lead.id, sent_by_user_id=user.id,
        conversation_id=convo.id,
    )
    if sent.blocked:
        raise email_outbound.OutboundError(sent.blocked, status=409)
    if not sent.ok:
        raise email_outbound.OutboundError(
            sent.detail or "The email could not be sent.", status=502,
        )

    reply_row = Message(
        conversation_id=convo.id, role="rep", content=text,
        tool_calls_json=_dump([{"name": "outreach", "outreach_id": sent.record.id}]),
    )
    db.add(reply_row)
    db.commit()
    db.refresh(reply_row)
    return reply_row


def _hand_over(db: Session, lead: Lead, why: str) -> None:
    """Something a person has to read. Raised where the queues already look.

    Not silence. On email there is no window the buyer is sitting in, so
    nothing happening reads as nobody having opened it -- which is the one
    outcome worse than a slow answer.
    """
    from app.agent.tools import escalate_to_human

    convo = thread_for(db, lead)
    # The same executor a model would call, not a second way of raising one --
    # `claim_for_owner` lives inside it, and an escalation that skipped it
    # would put an owned buyer back in "Needs a person" next to the name of
    # the rep who already has them.
    escalate_to_human(
        db, convo,
        {"rule_key": "", "reason": f"An email needs a person: {why}"},
        f"email-handover-{convo.id}",
    )


def _dump(calls: list[dict]) -> str:
    import json

    return json.dumps(calls) if calls else "[]"


def enabled_note() -> str:
    """One line for the setup page, when the agent is off in `.env`."""
    return (
        "EMAIL_AGENT is not set, so Liner does not answer email on this "
        "deployment." if not settings.email_agent else ""
    )


# ---------------------------------------------------------------------------
# The wait
# ---------------------------------------------------------------------------
#
# **Every reply waits, including the first.** Answering three seconds after a
# buyer wrote is the most robotic thing a mailbox can do, and the wait buys
# something besides: a window in which a rep can read the message and take the
# thread over before anything goes out on its own. It is the same number either
# way -- `EMAIL_REPLY_COOLDOWN_MINUTES` -- so a gap *between* replies falls out
# of it rather than being a second rule.


def schedule(
    db: Session, claim: InboundEmail, lead: Lead, received: Outreach, *, automated: str = ""
) -> dict:
    """Queue a reply for later, or say why there will not be one.

    The brakes that can be decided *now* are decided now, so a refusal reaches
    the receipt while somebody is still looking at it. The ones that depend on
    what happens next -- a rep answering, the switch being thrown, the hourly
    ceiling -- are re-run when it comes due, because that is the whole point of
    waiting.
    """
    verdict = email_agent.switched_on(db)
    if not verdict.allowed:
        return {"queued": False, "reason": verdict.reason, "detail": verdict.detail}
    if automated:
        return {
            "queued": False, "reason": "automated",
            "detail": f"No reply: {automated}.",
        }
    _, refused = readable(claim.body)
    if refused:
        _hand_over(db, lead, refused)
        return {"queued": False, "reason": "handed_over", "detail": refused}

    due = utcnow() + timedelta(minutes=max(settings.email_reply_cooldown_minutes, 0))
    row = EmailReplyDue(
        inbound_email_id=claim.id, lead_id=lead.id, outreach_id=received.id,
        due_at=due, automated=automated,
    )
    db.add(row)
    db.commit()
    return {"queued": True, "due_at": due, "id": row.id}


def due_now(db: Session, *, limit: int = 20) -> list[EmailReplyDue]:
    return (
        db.query(EmailReplyDue)
        .filter(EmailReplyDue.state == "waiting", EmailReplyDue.due_at <= utcnow())
        .order_by(EmailReplyDue.due_at.asc())
        .limit(limit)
        .all()
    )


def send_due(db: Session, row: EmailReplyDue, *, provider=None) -> dict:
    """Answer one queued reply, re-running every brake at the moment it fires.

    Re-run rather than trusted: the wait exists so that a person can get there
    first, and a decision taken minutes ago would defeat it. A rep having
    replied in the meantime is the ordinary outcome and is recorded as
    `skipped`, not as a failure.
    """
    claim = db.query(InboundEmail).filter_by(id=row.inbound_email_id).one_or_none()
    lead = db.query(Lead).filter_by(id=row.lead_id).one_or_none()
    received = (
        db.query(Outreach).filter_by(id=row.outreach_id).one_or_none()
        if row.outreach_id else None
    )
    if claim is None or lead is None or received is None:
        row.state, row.detail = "skipped", "The message it answered is gone."
        row.resolved_at = utcnow()
        db.commit()
        return {"sent": False, "reason": "gone"}

    # Anything that actually went out since they wrote means the answer has
    # been given -- by a rep, or by an earlier queued reply. One clock,
    # whoever wrote. `outreach_status.WENT_OUT` (added here): a queued or
    # failed send never reached the buyer, so it must not be read as "already
    # answered" -- that let a failed attempt silently cancel a reply that was
    # actually still owed (item 36).
    answered = (
        db.query(Outreach)
        .filter(
            Outreach.lead_id == lead.id,
            Outreach.channel == "email",
            outreach_status.WENT_OUT,
            Outreach.created_at >= row.created_at,
        )
        .first()
    )
    if answered is not None:
        row.state = "skipped"
        row.detail = (
            "A person answered first."
            if answered.sent_by_user_id else
            "Already answered by an earlier queued reply."
        )
        row.resolved_at = utcnow()
        db.commit()
        return {"sent": False, "reason": "already_answered", "detail": row.detail}

    out = answer(db, claim, lead, received, automated=row.automated, provider=provider)
    row.state = "sent" if out.get("sent") else (
        "skipped" if out.get("reason") in
        ("rep_holding", "cooldown", "person_answered", "switched_off", "off_in_env")
        else "failed"
    )
    row.detail = out.get("detail", "")
    row.resolved_at = utcnow()
    db.commit()
    return out
