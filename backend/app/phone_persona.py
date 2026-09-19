"""Liner's own assistant, for Liner's own phone number.

**Not the dealership's, and it deliberately cannot become it.** The buyer-facing
assistant has nine tools reaching a dealership's inventory, calendar and buyer
list; this one has two and reaches `ops_demo_requests`. That is the realm split
the `ops_` prefix exists to enforce, arriving on a new channel: a caller asking
Liner about Liner must not be able to walk into somebody's showroom data, and
the way to guarantee that is to hand the model no tool that reaches it.

Which of the two answers is `flags.phone_persona`, thrown from `/ops/phone`
without a restart -- so the same number can be handed to a prospect mid-demo
and answer as the assistant their own buyers would get.

The brief is short for the reason `agent/prompts.py` is short: a model given
two thirds of a script answers like one. What it needs to know is who it works
for, that it cannot promise anything technical, and that the job of the call is
a demo in the calendar.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

from app import demo_slots, flags
from app.config import settings
from app.db import ops_session, utcnow
from app.models import DemoRequest, PhoneCall

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: How many times to offer. Two is the number the dealership's assistant offers
#: and the number a person can hold in their head on a phone call.
OFFER = 12


BRIEF = """
================================================================
## WHAT YOU ARE DOING
================================================================
You are the assistant for Liner AI, and you are answering Liner's own phone.
Whoever is calling runs or works at a car dealership. You are an AI; if you are
asked, say so straight away and warmly.

Liner is an AI assistant a dealership puts on its own website and phone line.
It answers buyers day and night, searches the dealership's real inventory,
answers their written policy questions, captures a name and a number, and books
test drives straight into the calendar. Every car fact it gives a buyer comes
out of that dealership's own stock, and anything it is unsure of goes to a
person. One dealership per install.

Every turn does one of three things, and you pick which by listening:

  1. **Answer what they asked.** About what Liner does, what it costs to try,
     how long setting it up takes, what happens to their existing phone line.
  2. **Find out about them.** Which dealership, roughly how big, what is
     bothering them about the leads they get now. One question at a time.
  3. **Book the demo.** The moment they sound interested, offer two real times
     from check_demo_slots. That is what this call is for.

THINGS YOU DO NOT KNOW AND MUST NOT INVENT: pricing, contract terms, discounts,
what integrates with which CRM or DMS, a launch date, or how long anything
takes to build. You have no pricing page to read from. Say a founder will go
through it on the demo -- which is true, and is the reason the demo exists --
and book them in. Never quote a number of any kind.

Never claim a dealership uses Liner. Never name a customer. If they ask who
else is on it, say the honest thing: it is early, and the demo is a real
working system against their own inventory rather than a slide deck.

Warm, brief and specific. Two sentences, then stop and let them speak.
"""


VOICE_RULES = """
ON A PHONE CALL
They cannot see anything. Words only -- no markdown, no lists, no symbols, no
URLs. Say numbers the way people say them: two thirty on Thursday.

Two sentences, then stop. If they cut in, they have the floor. Answer in
English whatever they speak. Never say the caller's side or answer a question
nobody asked; if nothing was said to you, stay silent.

BOOKING
There is no screen, so you are the form. Call check_demo_slots and offer two
real times. Take their name, their dealership, and an email for the invitation
-- spell the address back and wait for a yes before you call book_demo. A
misheard address is a demo nobody turns up to. We already have the number they
are calling from, so do not ask for it.

Before you book, ask plainly whether it is all right for Liner to contact them
about the demo, and pass consent only if they actually say yes.

ENDING
Ask whether there is anything else you can help with. When they are done, say
your goodbye and call end_call in the same turn -- that is what puts the phone
down. Say nothing after it.
"""


TOOL_DEFS: list[dict] = [
    {
        "name": "check_demo_slots",
        "description": (
            "Open times for a Liner demo. Always offer two concrete times from this "
            "result; never ask an open 'when suits you?' and wait. You have no "
            "knowledge of the calendar without it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "description": "Defaults to a fortnight."},
            },
        },
    },
    {
        "name": "book_demo",
        "description": (
            "Book the demo. Needs their name, their dealership and an email address "
            "for the invitation -- read the address back and get a yes before "
            "calling this. Their phone number is already known from the call, so do "
            "not ask for it. Pass consent=true only if they said out loud that we "
            "may contact them about it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dealership": {"type": "string"},
                "email": {"type": "string"},
                "starts_at": {"type": "string", "description": "ISO 8601, from check_demo_slots"},
                "notes": {
                    "type": "string",
                    "description": "What they said they wanted, in one or two sentences.",
                },
                "consent": {
                    "type": "boolean",
                    "description": "True only if they agreed out loud to be contacted.",
                },
            },
            "required": ["name", "dealership", "email", "starts_at", "consent"],
        },
    },
    {
        "name": "end_call",
        "description": (
            "Call this ONLY when the caller has said they are done. Say your goodbye "
            "in the same turn -- this is what puts the phone down."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Two sentences a founder can read in five seconds.",
                },
            },
            "required": ["summary"],
        },
    },
]


class ToolError(Exception):
    """A tool refused. The message goes back to the model as a tool result."""


def _when(value: datetime) -> str:
    return f"{value:%A} {value.day} {value:%B} at " + (
        f"{(value.hour % 12) or 12}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"
    )


def check_demo_slots(db: Session, call: PhoneCall, args: dict) -> dict:
    slots = demo_slots.open_slots(db, int(args.get("days_ahead") or 0))[:OFFER]
    return {
        "timezone": settings.demo_timezone,
        "slots": [s.isoformat() for s in slots],
        "spoken": [_when(s) for s in slots[:4]],
    }


def book_demo(db: Session, call: PhoneCall, args: dict) -> dict:
    name = (args.get("name") or "").strip()
    dealership = (args.get("dealership") or "").strip()
    email = (args.get("email") or "").strip().lower()
    notes = (args.get("notes") or "").strip()

    if not name:
        raise ToolError("A name is needed to book the demo.")
    if not dealership:
        raise ToolError("Ask which dealership they are with -- it goes on the invitation.")
    if not EMAIL_RE.match(email):
        raise ToolError(
            f"'{email}' is not a valid email address. Spell it back to them and check "
            "it -- the invitation has nowhere else to go."
        )
    if not args.get("consent"):
        # The tick on the web form, said out loud. Refused rather than assumed
        # for the same reason the form refuses: the record exists to show that
        # somebody agreed, and one taken without asking shows nothing.
        raise ToolError(
            "Ask whether it is all right for Liner to contact them about the demo, "
            "and call this again once they have said yes."
        )

    try:
        at = datetime.fromisoformat(str(args["starts_at"]).replace("Z", ""))
    except (KeyError, ValueError) as exc:
        raise ToolError("starts_at must be a time from check_demo_slots.") from exc
    if at.tzinfo is not None:
        at = at.replace(tzinfo=None)

    # Re-decided now, never at the moment it was offered. The caller has been
    # talking for a minute or two since, and "still free then" is not an answer
    # -- the same rule the public form and `book_appointment` both follow.
    if at not in demo_slots.open_slots(db):
        raise ToolError(
            f"{_when(at)} has just been taken. Call check_demo_slots again and offer "
            "what is still open."
        )

    row = demo_slots.create(
        db,
        fields={
            "name": name,
            "dealership": dealership,
            "email": email,
            # Caller ID, which the carrier verified -- a better number than one
            # read out and transcribed, and the reason the prompt says not to
            # ask for it.
            "phone": call.from_number or "",
            "dealership_url": "",
            "message": notes,
        },
        slot=at,
        consent_text=demo_slots.PHONE_CONSENT,
        source="phone",
    )
    call.demo_request_id = row.id
    db.commit()
    return {
        "booked": True,
        "request_id": row.id,
        "starts_at": row.slot_at.isoformat(),
        "spoken": _when(row.slot_at),
        "timezone": settings.demo_timezone,
    }


def end_call(db: Session, call: PhoneCall, args: dict) -> dict:
    summary = (args.get("summary") or "").strip()
    if not summary:
        raise ToolError("end_call needs a summary of what happened.")
    call.status = call.status or "completed"
    call.ended_at = call.ended_at or utcnow()
    db.commit()
    return {"closed": True, "summary": summary}


EXECUTORS = {
    "check_demo_slots": check_demo_slots,
    "book_demo": book_demo,
    "end_call": end_call,
}


def execute(db: Session, call: PhoneCall, name: str, args: dict) -> dict:
    """Run one of ours. Unknown names are refused rather than ignored: a model
    calling a tool that silently returns nothing waits for ever."""
    runner = EXECUTORS.get(name)
    if runner is None:
        raise ToolError(
            f"{name} is not a tool on this call. You have: {', '.join(EXECUTORS)}."
        )
    return runner(db, call, args)


def instructions() -> str:
    """The whole prompt for Liner's own line.

    Takes no database, unlike the dealership's `build_system_prompt`: there is
    no row anywhere describing Liner to itself, and inventing a settings table
    so the two signatures matched would be a table with one row nobody edits.
    """
    return "\n".join([BRIEF.strip(), VOICE_RULES.strip()])


def demo_for(db: Session, call: PhoneCall) -> DemoRequest | None:
    """The demo this call booked, from Liner's own database.

    `db` is a dealership's and is ignored -- `ops_demo_requests` is not in it.
    """
    if not call.demo_request_id:
        return None
    with ops_session() as ops:
        row = ops.query(DemoRequest).filter_by(id=call.demo_request_id).one_or_none()
        if row is not None:
            ops.expunge(row)
        return row


#: Named here rather than imported from `flags` at every call site, so the two
#: personas are one word each wherever they are compared.
LINER = flags.PHONE_LINER
DEALERSHIP = flags.PHONE_DEALERSHIP
