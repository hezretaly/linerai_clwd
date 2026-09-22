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
    lead: Lead,
    convo: Conversation | None,
    *,
    instruction: str,
    rewrite: str = "",
) -> str:
    """Everything the draft may use, as one block appended to the prompt.

    `instruction` is the rep's one line -- "ask if Saturday works", "answer
    their financing question". It is the whole point of the feature: without
    it a draft is one guess at what the rep wanted, and they rewrite it by
    hand. `rewrite` is the other mode: the rep's own text, to be put in the
    dealership's voice with its facts kept.
    """
    parts: list[str] = ["--- WHAT YOU ARE DRAFTING ---"]

    if rewrite.strip():
        parts.append(
            "The team member has written a draft of their own, below. Rewrite it "
            "in the dealership's voice: keep every fact they stated and every "
            "commitment they made, change nothing about what is being offered, "
            "and do not add a claim they did not make. If something they wrote "
            "cannot be supported by the facts below, leave it exactly as they "
            "wrote it rather than correcting it -- it is their message and they "
            "may know something you do not.\n\n"
            f"THEIR DRAFT:\n{rewrite.strip()}"
        )
    if instruction.strip():
        parts.append(f"WHAT THEY ASKED FOR: {instruction.strip()}")
    if not rewrite.strip() and not instruction.strip():
        parts.append(
            "No specific instruction was given. Write the obvious next message "
            "to this buyer given where the conversation got to."
        )

    parts.append(_buyer_block(db, lead))
    parts.append(_dealership_block(db))

    vehicle = _focus_vehicle(db, lead, convo)
    if vehicle is not None:
        parts.append(_vehicle_block(db, vehicle))

    knowledge = _knowledge_block(db)
    if knowledge:
        parts.append(knowledge)

    parts.append(
        "--- RULES FOR THIS DRAFT ---\n"
        "Write the email body only. A team member reads it before anything is "
        "sent, so do not write as though it has already gone. Every fact you "
        "state must come from the blocks above: no price, mileage, feature or "
        "vehicle that is not written there, and no policy answer you compose "
        "yourself. If what they asked for needs something you have not been "
        "given, say plainly in the draft that a colleague will confirm it "
        "rather than filling the gap."
    )
    return "\n\n".join(p for p in parts if p)


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
            f"What may be said about price: {live.price_mode}",
            f"Financing: {live.financing_mode}",
        ]
    lines.append(
        "Do not sign the email. The system appends this dealership's own "
        "sign-off, and a second one reads as a mistake."
    )
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


def _focus_vehicle(db: Session, lead: Lead, convo: Conversation | None) -> Vehicle | None:
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
        "here -- say a colleague will confirm it.",
    ]
    for e in entries:
        lines.append(f"  {e.topic}: {e.answer}")
    return "\n".join(lines)
