"""The owner's two priorities, told to the model on the turn they apply to.

The dealership's own prompt says them, and so does `prompts.OPERATING_RULES`:
any question about paying for the car gets the credit application, and once a
buyer's name and number are on file the next step is a visit. Both are asks
for a tool call -- and the second call in a turn is the one a model drops. The
escalation that promised a colleague and never asked for a number is the case
on record (CLAUDE.md, "the escalation draws the card itself"). So on chat and
email this module reads the turn itself and, when one of them applies, says so
in a few lines appended for this turn only (`loop.run_turn`'s addendum), the
way the quiet-buyer follow-up is told its situation rather than being handed a
user message nobody typed.

Deterministic and read off rows: the buyer's latest message, the tool calls
already in the thread, the lead's phone and appointments. No model decides
whether a note applies, so the gate can drive every branch with no key.

**A call has no per-turn hook.** A realtime session is minted once with its
instructions and the audio never passes through this server, so for a call
the rules in `OPERATING_RULES` are the whole of it. `note` returns nothing for
`voice`.
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Appointment, Conversation, Lead, Message

#: Words that make a message a question about paying for the car. Curated
#: rather than clever, and in one place: this decides when the model is told to
#: put the finance application on screen, and a word list is something a
#: person can read and argue with. Each entry is a whole-word pattern, because
#: a substring is how "interested" became a question about interest and "do"
#: inside "Dodge" once ranked a Hornet above every Corvette.
#:
#: Left out on purpose: "afford" and a budget -- "something under 20k" is a
#: search, and the rules already say a budget is answered with cars; "deposit",
#: which on a lot usually means holding a car and is a policy answer from the
#: knowledge table; and a bare "rate", which is as often "first-rate" as APR.
MONEY_WORDS = re.compile(
    r"""
      \bcredit\b(?!\s*cards?\b)               # credit, bad credit -- not a card
    | \bfinanc\w*                             # finance, financing, financed
    | \bloans?\b
    | \bleas(?:e|es|ed|ing)\b
    | \bdown[\s-]?payments?\b
    | \b(?:much|need|put|putting|money|nothing|zero|cash)\s+down\b
    | \$\s?[\d,]+k?\s+down\b                  # "$2,000 down"
    | \bmonthly\b
    | \bpayments?\b
    | \bper\s+month\b
    | (?:\$\s?)?\b\d[\d,]*\s*(?:a|per|/)\s*mo(?:nth)?\b   # "$300 a month", "400/mo"
    | \bapr\b
    | \binterest(?:\s+rates?)?\b              # not "interested"
    | \b(?:pre-?)?approv(?:al|ed|e)\b           # approved, pre-approval
    | \bco-?sign(?:er|ers)?\b
    | \bbankrupt\w*
    """,
    re.IGNORECASE | re.VERBOSE,
)

#: A buyer saying they are *not* financing. "No financing, I'm paying cash"
#: names financing and is the opposite of a question about it: telling the
#: model to push the application at somebody who has just said they do not
#: need one is the pushiness the dealership's own prompt rules out. Such a
#: message gets no note. The rules still say money questions get the
#: application, so a buyer who is genuinely asking about both still can be
#: offered it -- that is left to the model reading the sentence.
#:
#: **Credit is never on this list.** "No credit" is a buyer with no credit
#: history -- exactly who the application is for -- not one declining it.
PAYING_CASH = re.compile(
    r"""
      \b(?:pay|paying|paid|in|all)\s+cash\b
    | (?<!not\sa\s)\bcash\s+(?:buyer|deal|purchase|offer)\b
    | \b(?:no|not|without|don'?t\s+need|won'?t\s+need|do\s+not\s+need)
        \s+(?:any\s+)?(?:financing|finance|a\s+loan|loan)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def about_money(text: str) -> bool:
    """Is this message a question about paying for the car?"""
    text = text or ""
    return bool(MONEY_WORDS.search(text)) and not PAYING_CASH.search(text)


def _calls(message: Message) -> list[dict]:
    """The tool calls a message carries. Tolerant, because the column holds
    a mirror's marker on a buyer message as well as a turn's calls."""
    try:
        calls = json.loads(message.tool_calls_json or "[]")
    except ValueError:
        return []
    return [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []


def credit_offered(db: Session, convo: Conversation) -> bool:
    """Has this thread already been given the finance application?

    Read off the calls that drew it: a result that came back available (a
    button in chat, a link by email). Not whether it was *pressed* -- a press
    goes through the counted hop, which files an anonymous `link_clicks` row
    that names no conversation, so "offered and unused" is not something this
    system can tell apart from "offered". Pointing a buyer who did press it at
    the same button is still right.
    """
    for message in (
        db.query(Message)
        .filter(Message.conversation_id == convo.id, Message.tool_calls_json.is_not(None))
        .all()
    ):
        for call in _calls(message):
            result = call.get("result") or {}
            if (call.get("name") == "offer_credit_application"
                    and isinstance(result, dict) and result.get("available")):
                return True
    return False


def _credit_link(db: Session) -> str:
    from app.api.settings import live_settings

    return (live_settings(db).credit_application_url or "").strip()


def booked(db: Session, lead_id: str) -> bool:
    """Has this buyer a visit still ahead -- booked or confirmed, not past?

    Past-and-never-marked counts as nothing booked, for the reason
    `check_availability` counts only future slots as taken: a Tuesday that
    has gone is not a reason to stop offering a Saturday.
    """
    return (
        db.query(Appointment)
        .filter(
            Appointment.lead_id == lead_id,
            Appointment.status.in_(("booked", "confirmed")),
            Appointment.starts_at >= utcnow(),
        )
        .first()
        is not None
    )


#: The two things that put a number on the row inside a turn: the contact
#: card's submit, and `save_captured_fields` saving a contact value.
def _gave_contact(message: Message) -> bool:
    for call in _calls(message):
        result = call.get("result") or {}
        if not isinstance(result, dict):
            continue
        if call.get("name") == "save_details" and result.get("lead_id"):
            return True
        if call.get("name") == "save_captured_fields" and (result.get("contact") or {}).get("phone"):
            return True
    return False


def contact_just_given(db: Session, convo: Conversation) -> bool:
    """Did the buyer's number go on file on the turn before this one, with
    nothing booked since?

    **The turn before, not any turn.** Told every turn from then on, the
    model would steer every reply at a booking whatever the push level on the
    Behaviour tab says -- the rules and that setting carry the rest of the
    conversation. What this adds is the one moment the owner named: the number
    has just arrived, and that is when to offer a visit.
    """
    if not convo.lead_id:
        return False
    lead = db.query(Lead).filter(Lead.id == convo.lead_id).one_or_none()
    if lead is None or not (lead.phone or "").strip() or booked(db, lead.id):
        return False
    # The latest thing the dealership's side said in this thread. The turn
    # being written now is not recorded yet, so this is the previous one: the
    # card's own "Got it" after a submit, or the reply that saved a number.
    previous = (
        db.query(Message)
        .filter(Message.conversation_id == convo.id, Message.role.in_(("assistant", "liner")))
        .order_by(Message.created_at.desc(), Message.id.desc())
        .first()
    )
    return previous is not None and _gave_contact(previous)


#: What the model is told, per case. Short: they are appended to a prompt
#: that is already long, on the turns they apply to.
CREDIT_NOW = """
THIS TURN: A QUESTION ABOUT PAYING FOR THE CAR
Their message is about credit, financing or payments. Call
offer_credit_application in this reply -- applying is the next step whatever
the numbers turn out to be -- and answer the rest of what they said as usual.
Never work out a rate, a payment or an approval yourself.
"""

CREDIT_AGAIN = {
    "chat": """
THIS TURN: A QUESTION ABOUT PAYING FOR THE CAR
The finance application button is already on their screen from earlier in
this chat. Point them back to it in one line; do not call
offer_credit_application again. Never work out a rate, a payment or an
approval yourself.
""",
    # An inbox has no screen to point at, and an earlier email is one the
    # buyer has to go and find: the link goes in again.
    "email": """
THIS TURN: A QUESTION ABOUT PAYING FOR THE CAR
The finance application went to them in an earlier reply. Call
offer_credit_application again so the link is in this email too, and say it
is the same application. Never work out a rate, a payment or an approval
yourself.
""",
}

BOOK_NOW = """
THIS TURN: THEIR NUMBER HAS JUST COME IN
Their name and number are on file now and nothing is booked, so the next step
is a visit or a test drive. If they have said they would like to come in, call
check_availability now. Otherwise answer them, then offer in one line to check
what times are open.
"""


def note(db: Session, convo: Conversation, text: str, channel: str) -> str:
    """This turn's instructions, "" when neither priority applies.

    `text` is the buyer's latest message ("" on a follow-up nobody typed).
    """
    if channel not in ("chat", "email"):
        return ""
    parts: list[str] = []
    if about_money(text) and _credit_link(db):
        # With no link set there is nothing to offer: the tool says so and
        # hands over to a person, which the rules already cover.
        parts.append(
            CREDIT_AGAIN[channel] if credit_offered(db, convo) else CREDIT_NOW
        )
    if contact_just_given(db, convo):
        parts.append(BOOK_NOW)
    return "\n".join(p.strip() for p in parts)
