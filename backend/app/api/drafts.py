"""The writing assistant: Auto-generate or Polish, on any composer a rep uses.

One endpoint for the buyer page's three boxes -- the email, the text and the
chat reply -- because they are one act: a rep asks for words, reads them, and
decides whether they leave. **Which of the two it does is decided by the box,
not by a second button**: empty is Auto-generate, the obvious next message;
anything typed is Polish, the rep's own words (a finished draft or three of
notes) turned into the message they meant with their facts kept. The words
in the box are the steering, so there is no separate instruction field.

**Nothing is sent and nothing is stored.** The text comes back and lives in
the rep's browser until they press Send, which goes through the composer's
own endpoint and `blocked_reason` exactly as a hand-typed message does.

**It cannot act.** `loop.draft_text` withholds the tool schema, so the model
that writes this cannot book the visit it offers, close the thread or raise a
handoff -- the buyer loop would have done all three as a side effect of
writing a paragraph, and through `may_reply`'s hourly ceiling could have
thrown the email kill switch.

**`have_model` is the one brake that applies.** The autonomous-reply
switches exist to stop Liner answering on its own; a person asked for this.
With the stub there is nothing to write with, and the answer is a typed 503
naming the setting rather than a template the rep cannot tell from a draft.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import email_agent, email_draft
from app.agent import loop
from app.agent.phrasing import plain
from app.api.deps import current_user
from app.db import get_db
from app.models import Conversation, Lead, User

router = APIRouter(tags=["drafts"])

#: Where a draft can be going. Closed, because it picks the shape asked for.
CHANNELS = ("email", "sms", "chat")


class DraftBody(BaseModel):
    channel: str = "email"
    #: What is in the box. Empty generates; anything else is polished.
    text: str = ""
    #: The subject line the rep typed, for a new email. Polished with the body;
    #: with an empty body it is what the generated email goes under.
    subject: str = ""
    #: The buyer, for an email or a text. A chat reply can be to somebody who
    #: has not said who they are, so it names the thread instead.
    lead_id: str = ""
    #: Which thread this is about. For a buyer, defaults to their newest.
    conversation_id: str = ""
    #: The email a reply or a forward is about, as the reader names it:
    #: `message` (an outreach row) or `unmatched` (a receipt nobody placed).
    answering_kind: str = ""
    answering_id: str = ""
    #: `reply`, `reply_all` or `forward`.
    how: str = ""
    #: Who a forward is going to, as typed in its To box.
    forward_to: str = ""


@router.get("/drafts/available")
def available(user: User = Depends(current_user)) -> dict:
    """Whether the writing assistant can write anything on this deployment.

    Asked once by every composer, before the button is pressed: offering it
    and discovering the answer afterwards was a button that did nothing a rep
    could see. Its own endpoint rather than a field of `/reach`, because the
    chat reply box also serves buyers with no lead, who have no `/reach`.
    """
    verdict = email_agent.have_model()
    return {"available": verdict.allowed, "reason": "" if verdict.allowed else verdict.detail}


@router.post("/drafts")
def draft(
    body: DraftBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if body.channel not in CHANNELS:
        raise HTTPException(400, f"A draft is for one of: {', '.join(CHANNELS)}.")
    if (body.answering_kind or body.answering_id) and (
        body.channel != "email" or body.how not in ("reply", "reply_all", "forward")
    ):
        raise HTTPException(400, "Only an email is answered: reply, reply_all or forward.")
    verdict = email_agent.have_model()
    if not verdict.allowed:
        # Typed, and it names the setting: "why did nothing happen" is the
        # question a person actually has.
        raise HTTPException(503, detail={"reason": verdict.reason, "detail": verdict.detail})

    # **Answering one email, opened in the reader.** It is read through the
    # reader's own function -- same kinds, same refusals (mail addressed to
    # Liner is a 404 here too) -- so the draft answers exactly the message the
    # rep has open, and mail from somebody not on file yet can be answered
    # with no conversation at all.
    answering = None
    if body.answering_kind or body.answering_id:
        from app.api.mail_reader import read_dealer

        answering = read_dealer(body.answering_kind, body.answering_id, 0, db, user)
        if not body.lead_id and answering.get("lead_id"):
            body.lead_id = answering["lead_id"]

    lead = None
    if body.lead_id:
        lead = db.query(Lead).filter(Lead.id == body.lead_id).one_or_none()
        if lead is None:
            raise HTTPException(404, "No such buyer.")
    convo = None
    if body.conversation_id:
        query = db.query(Conversation).filter(Conversation.id == body.conversation_id)
        if lead is not None:
            # A thread named alongside a buyer must be theirs.
            query = query.filter(Conversation.lead_id == lead.id)
        convo = query.one_or_none()
        if convo is None:
            raise HTTPException(404, "No such conversation for this buyer.")
        if lead is None and convo.lead_id:
            lead = db.query(Lead).filter(Lead.id == convo.lead_id).one_or_none()
    elif lead is not None:
        convo = (
            db.query(Conversation)
            .filter(Conversation.lead_id == lead.id)
            .order_by(Conversation.started_at.desc())
            .first()
        )
    if lead is None and convo is None and answering is None:
        raise HTTPException(400, "Name the buyer or the conversation to draft for.")
    if convo is None and answering is None:
        raise HTTPException(
            409,
            "This buyer has no conversation yet, so there is nothing to draft from. "
            "Write the first message yourself.",
        )

    polishing = bool(body.text.strip())
    request = (
        f"email_{'forward' if body.how == 'forward' else 'reply'}" if answering else body.channel
    )
    text, violations = loop.draft_text(
        db,
        convo,
        channel=request,
        polish=body.text,
        subject="" if answering else body.subject,
        brief=email_draft.brief(
            db, lead, convo,
            rewrite=body.text,
            # Written as the person pressing the button: it goes out under
            # their name, from their composer.
            author=user,
            channel=body.channel,
            answering=answering,
            how=body.how,
            forward_to=body.forward_to,
        ),
    )
    subject = ""
    if body.channel == "email":
        # A reply keeps the subject it answers, so one the model offered
        # anyway is dropped rather than handed back to replace it.
        subject, text = email_draft.split_subject(text)
        if answering:
            subject = ""
        elif not subject:
            # The model dropped the line: hand back theirs rather than a blank
            # the composer would have to know not to write over.
            subject = body.subject.strip()
    else:
        # A text and a chat bubble render no markdown, so none is sent: the
        # same cut `record_assistant_message` makes on Liner's own replies.
        text = plain(text)
    return {
        "mode": "polish" if polishing else "generate",
        # Empty when the model gave none; the composer then leaves the rep's
        # subject box alone rather than blanking it.
        "subject": subject,
        "body": text,
        # Shown to the rep rather than swallowed: the guards refused it twice,
        # and the rep is the person who can decide whether they know it.
        "violations": violations,
        "conversation_id": convo.id if convo is not None else None,
    }
