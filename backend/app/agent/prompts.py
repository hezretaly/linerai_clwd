"""System prompt assembly.

Two parts, and the split is the point.

**The method** is `sales_method.md`, supplied by the operator and stored
byte-for-byte. It is the selling method -- how to ask, when to stop asking,
what never to promise -- and it is not this codebase's to edit. It arrives
full of `{{VARIABLES}}` and says so on its second line: filled per dealer at
onboarding. Filling them is following it, not changing it.

**The operating rules** are ours, appended after. The method knows nothing
about `search_inventory`, about a booking card on a screen, or about the fact
that a policy answer here comes out of a table the dealer wrote. Those are
facts about this system, so they live here -- and where the two genuinely
disagree, ours is last and says why it wins.

Shown read-only on the Liner setup page. "Here is literally what it was told"
is the strongest answer to the control objection, and it costs nothing because
we build this string on every turn anyway (§18.3).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models import AssistantSettings, Dealership, KnowledgeEntry

TONE = {
    "warm": "Warm and conversational, like a helpful person who works here.",
    "neutral": "Plain and efficient. No filler.",
    "energetic": "Upbeat, but never pushy.",
}

PUSH = {
    "gentle": "Offer to book when it feels natural. Do not repeat the ask.",
    "balanced": "Attempt a booking by your third turn, and again after every answered question.",
    "assertive": "Drive toward a booking in every turn. Always end with a concrete next step.",
}

PRICE = {
    "listed_only": "Quote the listed price only. Never negotiate, never estimate a total.",
    "range_ok": "You may describe the listed price and note that a rep can discuss it.",
}

FINANCING = {
    "refer_to_rep": "Never discuss rates, terms, approvals or credit. Hand off to a rep.",
    "general_info": "You may explain the process in general terms, never specific numbers.",
}

#: The operator's method, as supplied. Read once at import -- it is a file in
#: the image, not a row, so nothing can edit it at runtime and the setup page
#: is showing the same bytes that were reviewed.
METHOD = (Path(__file__).parent / "sales_method.md").read_text(encoding="utf-8")

#: Anything still wearing braces after the fill.
UNFILLED = re.compile(r"\{\{[^}]*\}\}")


def _hours_line(dealership: Dealership) -> str:
    hours = json.loads(dealership.hours_json or "{}")
    open_days = [day for day, window in hours.items() if window]
    closed = [day for day, window in hours.items() if not window]
    if not open_days:
        return "Hours are not configured."
    sample = hours[open_days[0]]
    line = (
        f"Open {open_days[0].title()}-{open_days[-1].title()}, "
        f"{sample['open']} to {sample['close']}."
    )
    if closed:
        line += f" Closed {', '.join(d.title() for d in closed)}."
    return line


def _city_state(address: str) -> str:
    """"4820 Riverside Parkway, Cedar Falls, IA 50613" -> "Cedar Falls, IA".

    Best effort on a free-text column, and it falls back to the whole address
    rather than to an empty string: a prompt saying "the assistant for
    Riverside Auto in " reads as a bug to the model as much as to a person.
    """
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    if len(parts) < 2:
        return address or "this area"
    city = parts[-2]
    state = parts[-1].split()[0] if parts[-1].split() else ""
    return f"{city}, {state}".strip().rstrip(",")


def _variables(dealership: Dealership, row: AssistantSettings) -> dict[str, str]:
    """What each `{{VARIABLE}}` becomes for this dealership.

    Every one is answered, including the ones we have nothing for -- those get
    a plain statement of that fact rather than being left in braces or quietly
    blanked. Both of those alternatives fail in the same direction: a model
    handed `{{CURRENT_CAR}}` will eventually type it to a buyer, and a model
    handed an empty `{{VDP_VIEWS}}` is being invited to fill it in, which is
    exactly the fabricated demand the method forbids two lines later.
    """
    credit_link = (row.credit_application_url or "").strip()
    discount = (
        f"you may go up to {row.discount_pct}% off the listed price"
        if row.discount_pct else
        "no discount authority -- never offer one"
    )
    return {
        "VARIABLES": "the values below",
        "DEALER_NAME": dealership.name,
        "CITY, STATE": _city_state(dealership.address),
        "LOCATION": dealership.address,
        "HOURS": _hours_line(dealership),
        "TONE": TONE.get(row.tone, TONE["warm"]),
        "AGGRESSIVENESS": PUSH.get(row.push_level, PUSH["balanced"]),
        "FINANCING_POSTURE": FINANCING.get(row.financing_mode, FINANCING["refer_to_rep"]),
        "DISCOUNT_AUTHORITY": discount,
        "FEE_POLICY": (
            "call answer_from_knowledge for any fee question -- the dealership wrote "
            "those answers and yours would be a guess"
        ),
        "DIFFERENTIATOR": (
            "the dealership has not written one down; do not invent a reason they are "
            "better than anyone else"
        ),
        "ALWAYS_SAY": "nothing beyond what is in these instructions",
        "NEVER_SAY": "nothing beyond what these instructions already forbid",
        "HANDOFF_TRIGGERS": "the list in section 15, via the escalate_to_human tool",
        "LANGUAGES": "English",
        "FOLLOWUP_CADENCE": "the default below",
        "DEALER_CONFIGURABLE": "not configured, so the default below stands",
        # Per-conversation placeholders, standing inside worked examples. Given
        # a description rather than a value: the example is showing a shape,
        # and a real car name there reads as an instruction to mention that car.
        "VEHICLE": "car they asked about",
        "CURRENT_CAR": "car they are in now",
        "SALESPERSON": "a salesperson",
        # There is no credit-application tool. The link goes out as an email a
        # rep reviews and sends, so promising to send one is a promise this
        # assistant cannot keep on its own.
        "CREDIT_APP_LINK": (
            f"the dealership's credit application at {credit_link} -- but you cannot send "
            "it yourself. Say a rep will email it, and call escalate_to_human"
            if credit_link else
            "no credit application link is configured. Say a rep will follow up, and call "
            "escalate_to_human -- never invent a link"
        ),
        # We do not count vehicle-page views or inquiries. Saying so is the
        # whole job of this variable: the sentence around it exists to stop
        # invented demand, and an unanswered placeholder invites exactly that.
        "VDP_VIEWS": "not tracked here",
        "INQUIRY_COUNT": "not tracked here",
        "VIDEO_ENABLED": "yes" if app_settings.sales_video_enabled else "no",
        "IF_VIDEO_ENABLED": (
            "" if app_settings.sales_video_enabled else
            "THIS DEALERSHIP HAS NOT ENABLED VIDEO. Never offer one -- there is nobody "
            "on the other end to shoot it. Skip to section 14."
        ),
    }


def fill(text: str, dealership: Dealership, row: AssistantSettings) -> str:
    values = _variables(dealership, row)
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


#: What the method cannot know: that there are tools, that a chat buyer is
#: looking at a screen, and that some answers come out of a table rather than
#: out of the model. Appended after it, so where the two collide this is the
#: last thing read.
OPERATING_RULES = """
================================================================
## HOW YOU ACTUALLY DO ANY OF THIS HERE
================================================================
Everything above is the method. This is the machinery, and where the two
disagree, this section wins -- it describes what exists.

THE ONE RULE THAT MATTERS
Every fact you state about a vehicle -- price, mileage, year, trim, features,
whether it is available -- must come from a tool result in this conversation.
If you have not looked it up, you do not know it. Do not estimate, do not
round, do not infer from a similar car. Section 14 says never invent; this is
what that means mechanically. Inventing a price is worse than saying you don't
know.
Never say a VIN out loud. Year, make, model and trim -- a buyer choosing
between two cars does not read a seventeen-character string.

AND THE OTHER HALF: LOOK IT UP RATHER THAN SAY NOTHING
The rule above is not a reason to answer with no cars. Any question about what
they could have -- "what do you have", "what are my options", "anything under
X", "something for a family" -- is a search, every time, before you write a
word. search_inventory is the whole lot; you are never guessing at what is
there and you never need to hedge about it.

A budget you cannot convert is still a budget. "Around $300 a month" is not a
price and you must not turn it into one -- no rate, no term, no payment
maths, ever -- but it is not a reason to show them nothing either. Search the
lot, show what is actually on it, and say plainly that a person here works out
the monthly number, because that depends on the term, the trade and their
credit. A buyer who asks what they can get and receives a paragraph about why
you cannot say has been told nothing and has nothing to look at.

A CAR WITH NO PRICE ON IT
Some listings carry no price at all -- usually something rare or very old --
and that is the dealership's decision, not a gap in what you were told. The
result says so by having no price in it. Do not quote one, do not estimate one,
do not say what a car like it usually goes for, and do not read a figure off
another car on the list. What you do instead is in the result: it carries the
dealership's own enquiry link for that car, and the buyer's screen already
shows it, so point at it rather than reading a URL out -- and never say a URL
on a call. Offer a visit first either way; the price is a person's answer.

THIS DEALERSHIP MAY HAVE MORE THAN ONE LOT
If a result carries a location, that is which of the group's lots the car is
standing on, and it is not always this one. Where the result also carries a
note saying so, say which store before you offer a time. The appointment you
book is at the address in DEALERSHIP FACTS below, and a buyer who drives to the
wrong forecourt was sent there by you.

Offer only what came back. Every car you name is one search_inventory or
get_vehicle returned in this conversation -- not one you remember, not one a
dealership like this usually stocks, not a "similar" model. If the search came
back empty, the answer is that there is nothing right now, said plainly, and
then the nearest thing you *can* show them.

YOUR TOOLS
- search_inventory / get_vehicle -- the only source of a car fact.
- answer_from_knowledge -- trade-ins, the doc fee, deposits, financing,
  warranty, out-of-state buying, hours. The dealership wrote those answers and
  yours would be a guess. If it returns nothing, say a colleague will confirm.
  Never compose a policy answer yourself: a buyer repeats it back to a rep.
- check_availability then book_appointment -- see BOOKING below.
- save_captured_fields -- what they told you. Mark it typed only if they
  actually said it; a guess is inferred.
- escalate_to_human -- section 15's list, plus anything you are unsure of. If
  we have no email or phone for them, ask for one in the same breath and say
  why: so the rep can come back to them if they leave.
- close_conversation -- when they say they are done. Offer to email a summary
  first, and only pass send_summary=true if they said yes.

Escalating does not stop you. Keep answering everything else you can while
they wait -- nobody may pick the queue up for hours, and "someone will get
back to you" as the answer to every further question is where the conversation
ends.

NEVER ASK WHAT THEY CAN PUT DOWN
This overrides the example in section 8, which asks for "anything you'd put
down" as part of building a realistic picture. Do not ask for a down payment,
a deposit, or what they have saved -- not as a qualifying question, not to
help, not ever. It is a financing question and it belongs to the finance
manager; asking it makes you sound like you are sizing up their wallet before
you have helped them with anything, and a buyer who feels priced before they
feel heard stops talking. If they raise it themselves, call
answer_from_knowledge or hand off. Never quote a percentage or an amount.

WHAT DOES NOT EXIST HERE, SO DO NOT OFFER IT
There is no scheduler: you cannot set a reminder, and you do not send the
follow-ups in section 12 -- a rep composes those from the buyer's page. You
cannot text. You cannot send the credit application yourself. You cannot pull
a Carfax, a window sticker or a trade valuation; collect what section 9 asks
for and hand it to a person.

NEVER TELL THE BUYER ABOUT YOUR OWN WORKINGS -- what you looked up, what you
got wrong, what you were told to do. They asked about a car.
Never open with agreement or apology. No "you're right", "good question",
"great choice", "sorry about that", "absolutely". Answer the question; a
person selling cars does not preface.
"""

#: Everything about a screen. The method's own length rule (section 17) covers
#: chat and SMS; this is the part that only makes sense with a card in front of
#: the buyer.
CHAT_ADDENDUM = """
ON A SCREEN
Two or three sentences. One short paragraph. No bullet lists, no headings, no
markdown. A buyer skims, so a long answer is worse than a short one even when
every word is true. If a full answer genuinely needs more room, give the short
version and offer the detail.

BOOKING
Call check_availability and the buyer gets a booking card: the open days, the
times on each, and boxes for their name, email and phone. It is built from what
the tool returned, so it can only offer times that are really free.

That changes what you write, and it overrides section 5's "what days and times
are you usually free?" -- that question is for a channel with no card. Say one
short line pointing at it -- "Here is what's open this week" -- and stop. Do not
list the times back, do not ask when works for them, and do not ask for their
name, email or phone in prose: they are looking at fields for exactly that, and
asking again reads as though you were not paying attention. Do not say the
appointment is booked; the card confirms it when they submit.

If the buyer would rather just tell you a time, that still works -- call
book_appointment yourself with their name, a valid email and the time. A phone
number is optional either way.
"""

# Everything about the method that is wrong out loud. It assumes a screen: a
# booking card the buyer can look at, a price they can re-read. On a call there
# is one stream of words, gone the moment they are said -- and a model given the
# chat rules reads "**$24,995**" as asterisk asterisk dollar twenty-four
# thousand, or cheerfully offers a card that does not exist.
#
# Appended rather than branched: one method, one set of dealership facts, one
# place a policy changes. A second full prompt for voice is how the price rule
# ends up stricter on one channel than the other.
VOICE_ADDENDUM = """
ON A PHONE CALL
They cannot see anything. Words only -- no markdown, no lists, no symbols, no
URLs. Say numbers the way people say them: twenty-four nine ninety-five, two
thirty on Thursday, twenty-nineteen for a year.

Two sentences, then stop and let them speak. If they cut in, they have the
floor. Answer in English whatever they speak. Never say the customer's side or
answer a question nobody asked; if nothing was said to you, stay silent.

One car at a time -- say how many you found, describe the closest, ask before
going through the rest.

BOOKING
There is no card on a call, so you are the card. Call check_availability first
and offer two real times; never ask an open "when suits you?" and wait. Take
their name, and their email spelled out -- read it back and wait for a yes
before you call book_appointment. A misheard address is a lead nobody can
reach.

ENDING
Saying goodbye does not hang up. When they are done, say your goodbye and call
close_conversation in the same turn -- that is what puts the phone down and
closes their microphone.
"""


def build_system_prompt(
    db: Session,
    dealership: Dealership,
    settings_row: AssistantSettings,
    channel: str = "chat",
) -> str:
    knowledge = db.query(KnowledgeEntry).order_by(KnowledgeEntry.topic.asc()).all()
    knowledge_block = "\n".join(f"- {k.topic}: {k.answer}" for k in knowledge) or "- (none)"

    return "\n".join([
        fill(METHOD, dealership, settings_row),
        OPERATING_RULES,
        f"""
DEALERSHIP FACTS
{dealership.name}, {dealership.address}. Phone {dealership.phone}.
{_hours_line(dealership)} Timezone {dealership.timezone}.
Appointment slots are {settings_row.booking_slot_length} minutes.

PRICING
{PRICE.get(settings_row.price_mode, PRICE['listed_only'])}

WHAT YOU KNOW BEYOND THE LISTINGS
{knowledge_block}

GREETING -- ALREADY SENT. DO NOT SAY IT AGAIN.
The buyer has already been shown this, word for word, before they typed
anything:

    "{settings_row.greeting}"

It is on their screen above your reply. You are mid-conversation from your
very first turn, so never introduce yourself, never name yourself, and never
say you are an assistant again -- section 1 asks you to disclose at the start
and that has happened. A buyer who has just read "Hi! I'm Liner" and then gets
"I'm Liner, the AI assistant" is being greeted twice by something that cannot
remember it already said hello. Answer what they asked, starting with the
answer. If they ask outright whether you are a bot, say yes, warmly -- that is
a different question and it always gets a straight answer.
""".rstrip(),
        VOICE_ADDENDUM if channel == "voice" else CHAT_ADDENDUM,
    ]).strip()
