"""What a drafted email is allowed to know, composed from rows.

A rep presses **Draft with Liner** on a buyer's page and gets an email they
read, edit and decide to send. This module answers the only interesting
question in that sentence: *what does the draft get to see?*

**Composed here rather than fetched by the model.** `loop.draft_text` runs
with no tools at all, because `tools.execute` books appointments and closes
conversations and a draft must not do either -- so everything the draft knows
has to be assembled first. That is not a workaround; it is the same decision
`app/recap.py` made for the rail's summary. A model asked to go and find the
facts is a second place a fact can be invented, and this way the brief can be
read, printed and checked against the database.

What goes in, and why each one:

* **The recap**, from `app/recap.py` -- who this is, which car, the
  appointment, any open escalation. Already deterministic and already the
  thing a rep reads at the top of the page, so the draft and the rail cannot
  disagree about the state of a buyer.
* **The car in focus**, whole, through the same `_vehicle_payload` the
  assistant gets. This is the "car being asked about": its price, its
  mileage, its options list and -- where the dealership has written one --
  its history report. The guards check a price against what a tool returned,
  so handing over the real row is also what makes a legitimate price
  *sayable*.
* **The captured fields, with their provenance.** A rep is not a buyer: they
  are allowed to see that the budget was `inferred` rather than `typed`, and
  the brief says so, because prose cannot carry a badge and a guess repeated
  as fact is how somebody ends up arguing about a number nobody gave.
* **The dealership's own posture** -- its name, its address, its hours, and
  the tone and push level a manager set on the Liner setup page. That is the
  "company culture" half: the same settings that shape what the assistant
  says to a buyer shape what it drafts for a rep to send.
* **The knowledge table**, verbatim. Trade-ins, the doc fee, deposits: the
  dealer wrote those answers and `answer_from_knowledge` exists so a model
  never composes one. A draft quoting a policy has to quote *theirs*.

What stays out: anything the buyer must not read back. `internal_note` on a
vehicle ("no discount without Dana's approval") is a rule for the floor, and
a draft is a message *to the buyer* -- so it is stripped, exactly as
`buyer_tool_calls` strips it from the rehydrate.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agent import tools
from app.api.settings import live_settings
from app.models import (
    CapturedField,
    Conversation,
    Dealership,
    KnowledgeEntry,
    Lead,
    User,
    Vehicle,
)
from app.recap import lead_recap

#: How much of the knowledge table a draft carries. The whole of a big one is
#: a page of prose on every draft; the dealer's own answers are short and the
#: common ones come first.
MAX_KNOWLEDGE = 12

#: Rep-facing wording for what the provenance actually means. `typed` is the
#: buyer's own words; the rest is a guess wearing a label.
PROVENANCE = {
    "typed": "they said this",
    "caller_id": "from the caller ID",
    "inferred": "a guess, not confirmed",
}


def brief(
    db: Session,
    lead: Lead | None,
    convo: Conversation | None,
    *,
    instruction: str = "",
    rewrite: str = "",
    author: User | None = None,
    channel: str = "email",
    answering: dict | None = None,
    how: str = "",
    forward_to: str = "",
) -> str:
    """Everything the draft may use, as one block appended to the prompt.

    Two modes, and the composer's one button picks between them by whether
    the box has anything in it. Empty is **Auto-generate**: the obvious next
    message from where the conversation got to. Anything typed is **Polish**:
    `rewrite` is the rep's own text -- a finished draft or three words of
    notes, "ask if saturday works" -- turned into the message they meant,
    with their facts kept. The words in the box are the steering, which is
    why there is no separate instruction field any more; `instruction` stays
    for a caller that has one.

    `channel` is `email`, `sms` or `chat`, and decides the closing: an email
    ends with the rep's first name over the signature appended at send, a
    text or a chat reply ends when it has said its piece.
    """
    what = {"email": "email", "sms": "text message", "chat": "chat reply"}.get(channel, "message")
    parts: list[str] = ["--- WHAT YOU ARE DRAFTING ---", f"A {what} to this buyer."]

    # The one email this is answering or passing on, as the reader showed it.
    # First, because it is what the message is about.
    if answering:
        parts.append(_answering_block(answering, how, forward_to))

    if rewrite.strip():
        parts.append(
            f"The team member has written this {what} themselves, below -- it may "
            "be a finished draft or a few words of notes. Turn it into the message "
            "they meant, in the dealership's voice: keep every fact they stated "
            "and every commitment they made, change nothing about what is being "
            "offered, and do not add a claim they did not make. If something they "
            "wrote cannot be supported by the facts below, leave it exactly as "
            "they wrote it rather than correcting it -- it is their message and "
            "they may know something you do not.\n\n"
            f"THEIR DRAFT:\n{rewrite.strip()}"
        )
    if instruction.strip():
        parts.append(f"WHAT THEY ASKED FOR: {instruction.strip()}")
    if not rewrite.strip() and not instruction.strip():
        parts.append(
            "No specific instruction was given. Write the obvious next message "
            "to this buyer given where the conversation got to."
        )

    shop = db.query(Dealership).first()
    if author is not None:
        parts.append(_author_block(author, shop, channel))
    else:
        parts.append(
            "--- HOW IT ENDS ---\nDo not sign the email. The dealership's "
            "sign-off is appended when it is sent."
        )
    parts.append(_buyer_block(db, lead) if lead is not None else _stranger_block(db, convo))
    parts.append(_dealership_block(db))

    vehicle = _focus_vehicle(db, lead, convo)
    if vehicle is not None:
        parts.append(_vehicle_block(db, vehicle))

    knowledge = _knowledge_block(db)
    if knowledge:
        parts.append(knowledge)

    parts.append(
        "--- RULES FOR THIS DRAFT ---\n"
        f"Write the {what} only. A team member reads it before anything is "
        "sent, so do not write as though it has already gone. Every fact you "
        "state must come from the blocks above: no price, mileage, feature or "
        "vehicle that is not written there, and no policy answer you compose "
        "yourself. If what they asked for needs something you have not been "
        "given, say plainly that you will check it and come back to them "
        "rather than filling the gap."
    )
    return "\n\n".join(p for p in parts if p)


#: The dealership's price and financing settings, as rules for somebody
#: writing *as a rep*. The assistant's own wording (`prompts.PRICE`) ends in
#: "hand off to a rep", which is nonsense addressed to the rep. An unknown key
#: falls back to the strict one, as it does for the assistant.
DRAFT_PRICE = {
    "listed_only": "Quote the listed price only. Do not offer a discount or "
                   "estimate a total in writing.",
    "range_ok": "You may quote the listed price and say you are happy to talk "
                "about it in person.",
}
DRAFT_FINANCING = {
    "refer_to_rep": "Put no rates, terms, approvals or credit decisions in "
                    "writing; offer to go through financing with them in "
                    "person or on the phone.",
    "general_info": "You may explain the financing process in general terms, "
                    "never with specific numbers.",
}


#: How a role on the roster reads in a sentence a buyer sees. The stored
#: values are the system's words (`rep`), not a job title anybody signs with.
ROLE_TITLE = {"manager": "sales manager", "rep": "sales representative"}


def _author_block(author: User, shop: Dealership | None, channel: str = "email") -> str:
    """Who is writing: the person signed in, not the assistant.

    **A drafted email goes out under the rep's name**, because the composer
    that sends it is theirs and the sign-off appended to it is theirs
    (`with_signature` with `user=`). The system prompt around this brief is
    the buyer-facing assistant's -- "you are Liner" -- so without this the
    draft spoke as Liner, or as "our team", above a rep's own signature: an
    email in two voices, which a buyer reads as a template.

    **And it closes with their first name**, as a person writing their own
    email does. The block appended at send carries their full name and title
    over the dealership's details (`outreach_send.person_signature`), which is
    the usual shape of an email: a closing, then a signature.
    """
    title = ROLE_TITLE.get(author.role, author.role or "member of the team")
    where = f" at {shop.name}" if shop is not None and shop.name else ""
    first = (author.name or "").split()[0] if (author.name or "").strip() else ""
    closing = (
        f"End with a short closing and their first name on its own line, for "
        f"example \"Best,\\n{first or author.name}\". Their signature -- full "
        "name, title and the dealership's details -- is appended under it when "
        "it is sent, so do not write any of that."
        if channel == "email" else
        # A text and a chat reply carry no signature block: the buyer already
        # knows who they are talking to, and a sign-off under two sentences
        # reads as a form letter.
        "No closing and no signature block."
    )
    return (
        "--- WHO IS WRITING ---\n"
        f"{author.name}, {title}{where}. Write as them, in the first person "
        "(\"I\", and \"we\" for the dealership), never as Liner or as an "
        "assistant. They are a person the buyer can call back and ask for by "
        "name.\n"
        + closing
    )


def split_subject(text: str) -> tuple[str, str]:
    """`Subject: ...` off the first line of a draft, and the body under it.

    Asked for in `loop.DRAFT_REQUEST`; read leniently because a model will
    sometimes bold it or skip it. No subject line is not an error -- the body
    comes back whole and the rep's subject box is left as it was.
    """
    lines = (text or "").strip().splitlines()
    if lines:
        first = lines[0].strip().strip("*").strip()
        if first.lower().startswith("subject:"):
            subject = first.split(":", 1)[1].strip().strip("*").strip()
            return subject, "\n".join(lines[1:]).strip()
    return "", (text or "").strip()


def _buyer_block(db: Session, lead: Lead) -> str:
    lines = [
        "--- THE BUYER ---",
        f"Name: {lead.name or 'not given'}",
        f"Email: {lead.email or 'none on file'}",
        f"Phone: {lead.phone or 'none on file'}",
        f"Where they came from: {lead.source or 'not recorded'}",
        "",
        lead_recap(db, lead) or "No history recorded yet.",
    ]

    fields = (
        db.query(CapturedField)
        .filter(CapturedField.lead_id == lead.id)
        .order_by(CapturedField.updated_at.asc())
        .all()
    )
    if fields:
        lines.append("")
        lines.append("What Liner has captured, and how sure each one is:")
        for f in fields:
            how = PROVENANCE.get(f.provenance, f.provenance or "unknown")
            lines.append(f"  - {f.key}: {f.value}  ({how})")
        lines.append(
            "Do not repeat a guess back to them as though they said it. If a "
            "guessed field matters to the message, ask rather than assert."
        )
    return "\n".join(lines)


#: How much of an answered email the brief carries. A person's ask is at the
#: top, and the quoted history under a long thread is the part nobody reads.
ANSWERED_MAX = 4000


def _answering_block(content: dict, how: str, forward_to: str) -> str:
    """The email being answered or forwarded: who, when, what, and its words.

    Read through the reader's own `read_dealer`, so the draft answers exactly
    the message the rep has open -- not a guess at the latest one, which is
    the mistake the old inline composer's `lastInbound` made.
    """
    sender = content.get("from") or {}
    who = sender.get("name") or ""
    address = sender.get("address") or ""
    body = (content.get("text") or "").strip()
    if len(body) > ANSWERED_MAX:
        body = body[:ANSWERED_MAX] + "\n[... the rest is not shown]"
    forwarding = how == "forward"
    lines = [
        "--- THE EMAIL BEING FORWARDED ---" if forwarding else "--- THE EMAIL YOU ARE ANSWERING ---",
        f"From: {who} <{address}>" if who else f"From: {address}",
        f"Date: {content.get('date') or 'not recorded'}",
        f"Subject: {content.get('subject') or '(no subject)'}",
        "",
        body or "(no text)",
    ]
    if forwarding:
        lines += [
            "",
            f"It is being forwarded to: {forward_to.strip() or 'people not yet chosen'}. "
            "Write the note to them, not to whoever wrote it.",
        ]
    return "\n".join(lines)


def _stranger_block(db: Session, convo: Conversation | None) -> str:
    """A buyer who has not said who they are: a chat reply to somebody anonymous.

    Most live chats have no lead -- `book_appointment` is what mints one -- and
    the reply box on such a thread is exactly where a rep taking over wants a
    hand. There is no name to use and nothing captured against a person, so
    the block says so rather than inventing a "there" to greet.
    """
    from app.recap import conversation_recap

    lines = [
        "--- THE BUYER ---",
        "Not identified yet: no name, number or address on file. Do not guess one.",
    ]
    if convo is not None:
        lines += ["", conversation_recap(db, convo) or "No history recorded yet."]
    return "\n".join(lines)


def _dealership_block(db: Session) -> str:
    shop = db.query(Dealership).first()
    live = live_settings(db)
    lines = ["--- THE DEALERSHIP, AND HOW IT SOUNDS ---"]
    if shop is not None:
        lines += [
            f"Name: {shop.name}",
            f"Address: {shop.address}",
            f"Phone: {shop.phone}",
        ]
        hours = _hours(shop)
        if hours:
            lines.append(f"Opening hours: {hours}")
    if live is not None:
        lines += [
            f"Tone: {live.tone}",
            f"How hard to push: {live.push_level}",
            # The dealership's rule in words, not its setting's key:
            # `listed_only` means nothing to a model, and this is the one
            # posture a draft must keep now that it no longer runs under the
            # buyer assistant's prompt.
            f"Price: {DRAFT_PRICE.get(live.price_mode, DRAFT_PRICE['listed_only'])}",
            f"Financing: {DRAFT_FINANCING.get(live.financing_mode, DRAFT_FINANCING['refer_to_rep'])}",
        ]
    return "\n".join(lines)


def _hours(shop: Dealership) -> str:
    try:
        hours = json.loads(shop.hours_json or "{}")
    except ValueError:
        return ""
    open_days = [
        f"{day[:3].title()} {v.get('open')}-{v.get('close')}"
        for day, v in hours.items()
        if isinstance(v, dict) and v.get("open")
    ]
    return ", ".join(open_days)


def _focus_vehicle(db: Session, lead: Lead | None, convo: Conversation | None) -> Vehicle | None:
    """The car this is about: the thread's focus, else the last one quoted.

    Asked across the buyer rather than off the newest thread alone, for the
    reason `lead_recap` is: somebody who chatted about an X5 and rang back
    next morning has a focus on one conversation and a mention on another.
    """
    if convo is not None and convo.focus_vehicle_id:
        found = db.query(Vehicle).filter(Vehicle.id == convo.focus_vehicle_id).one_or_none()
        if found is not None:
            return found
    from app.models import VehicleMention

    if lead is None:
        return None
    mention = (
        db.query(VehicleMention)
        .join(Conversation, VehicleMention.conversation_id == Conversation.id)
        .filter(Conversation.lead_id == lead.id)
        .order_by(VehicleMention.created_at.desc())
        .first()
    )
    if mention is None:
        return None
    return db.query(Vehicle).filter(Vehicle.id == mention.vehicle_id).one_or_none()


def _vehicle_block(db: Session, vehicle: Vehicle) -> str:
    payload = tools._vehicle_payload(vehicle, tools.home_location(db))
    # A rule for the floor is not a line in a buyer's email. Same cut
    # `buyer_tool_calls` makes on the rehydrate, for the same reason.
    payload.pop("internal_note", None)
    return (
        "--- THE CAR THIS IS ABOUT ---\n"
        "Every figure you may quote about it is here. Do not state one that is "
        "not.\n"
        + json.dumps(payload, indent=1, default=str)
    )


def _knowledge_block(db: Session) -> str:
    entries = (
        db.query(KnowledgeEntry)
        .order_by(KnowledgeEntry.use_count.desc())
        .limit(MAX_KNOWLEDGE)
        .all()
    )
    if not entries:
        return ""
    lines = [
        "--- THE DEALERSHIP'S OWN ANSWERS ---",
        "Quote these verbatim where one applies. Do not compose your own "
        "version of a policy, and do not answer a policy question that is not "
        "here -- say you will confirm it.",
    ]
    for e in entries:
        lines.append(f"  {e.topic}: {e.answer}")
    return "\n".join(lines)
