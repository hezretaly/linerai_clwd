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

from app import email_agent, outreach_send
from app.config import settings
from app.db import utcnow
from app.email_intake import just_the_reply
from app.events import emit
from app.integrations.registry import get_email_sender
from app.models import Conversation, InboundEmail, Lead, Message, Outreach

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
    verdict = email_agent.may_reply(db, lead, automated=automated)
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

    db.add(Message(conversation_id=convo.id, role="buyer", content=text))
    db.commit()

    from app.agent.loop import run_turn

    reply, calls = run_turn(db, convo, text, provider, channel="email")
    db.add(Message(
        conversation_id=convo.id, role="liner", content=reply,
        tool_calls_json=_dump(calls),
    ))
    db.commit()

    # Checked again, immediately before the wire. `may_reply` ran before a
    # model round trip that takes seconds, and a rep who pressed Take over or
    # threw the kill switch during it must not be overtaken by a message that
    # was already in flight.
    again = email_agent.may_reply(db, lead, automated=automated)
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

    sender = get_email_sender()
    to = claim.from_address or lead.email
    subject = claim.subject or "Your enquiry"
    record = Outreach(
        lead_id=lead.id,
        # NULL, and that is the whole author test -- a rep's send carries their
        # id. The cooldown, the pause and every "did a person answer" question
        # read this rather than a column added for them.
        sent_by_user_id=None,
        channel="email",
        direction="out",
        kind="reply",
        to_address=to,
        subject=subject if subject.lower().startswith("re:") else f"Re: {subject}",
        body=reply,
        provider=sender.name,
        status="queued",
        reply_token=outreach_send.mint_reply_token(db),
        in_reply_to=received.provider_message_id or None,
    )
    db.add(record)
    db.commit()

    blocked = outreach_send.blocked_reason(sender, to)
    if blocked:
        record.status = "failed"
        record.error = blocked
        db.commit()
        return {"sent": False, "reason": "blocked", "detail": blocked,
                "outreach_id": record.id}

    try:
        result = sender.send(
            to, record.subject, record.body,
            reply_to=outreach_send.reply_to_address(record.reply_token),
            in_reply_to=record.in_reply_to or "",
            from_address=outreach_send.dealership_from(db, sender),
        )
    except Exception as exc:  # NotConfigured, or anything the provider raised
        record.status = "failed"
        record.error = str(exc)
        db.commit()
        return {"sent": False, "reason": "provider", "detail": str(exc),
                "outreach_id": record.id}

    record.provider_message_id = result.message_id
    record.status = result.status
    record.error = result.detail if result.status != "sent" else ""
    record.sent_at = utcnow()
    db.commit()
    emit(db, "outreach.sent", {
        "outreach_id": record.id, "appointment_id": None, "lead_id": lead.id,
        "to": to, "provider": record.provider,
        "delivered_externally": sender.delivers, "conversation_id": convo.id,
        "by_liner": True,
    })
    return {"sent": True, "reason": "", "outreach_id": record.id,
            "conversation_id": convo.id, "body": reply}


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
