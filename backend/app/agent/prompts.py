"""System prompt assembly.

**A brief, not a script.** `BRIEF` states the job in a paragraph -- every turn
either helps the buyer more, gets a way to reach them, or books them in -- and
leaves the selling to a model that already knows how to sell. `OPERATING_RULES`
follows it with the things no executor can enforce, and every one of those is
there because something went wrong once.

This replaced `sales_method.md`, 21KB of NEPQ script that was two thirds of
every prompt this system sent. A model handed two thirds of a script answers
like one: long, staged, and reluctant to just say what a car costs. The file
is **kept, not deleted** -- it is the operator's document -- and stays
reachable through `assistant: sales_method: true` in a dealership's profile,
because an archive nobody can switch on is a dead file.

The rest of the prompt is data rather than instruction: the dealership's own
facts, its pricing posture, the knowledge table it wrote, the greeting already
on the buyer's screen, and one channel addendum. **One prompt with an addendum,
never a prompt per channel** -- two is how the price rule ends up stricter on
one channel than another.

Shown read-only on the Liner setup page. "Here is literally what it was told"
is the strongest answer to the control objection, and it costs nothing because
we build this string on every turn anyway.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app import profile
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
#:
#: **Off by default now, and that is a deliberate reversal.** It is 21KB of
#: NEPQ script -- about two thirds of every prompt this system sends -- and a
#: model given two thirds of a script answers like one: long, staged, and
#: reluctant to just say what a car costs. What replaced it is `BRIEF` below,
#: which states the job in a paragraph and leaves the selling to a model that
#: already knows how to sell. The file is untouched and still reachable: a
#: dealership that wants the long method sets `assistant: sales_method: true`
#: in its profile, which is where a fact about the dealership belongs.
METHOD = (Path(__file__).parent / "sales_method.md").read_text(encoding="utf-8")

#: The whole method, in a paragraph. This is the prompt now.
#:
#: The three objectives are the operator's own words and they are written as a
#: choice rather than a sequence, because a script that must be walked in order
#: is what the 21KB one was. Every turn does one of the three; which one is the
#: model reading the buyer, which is the thing being paid for.
BRIEF = """
================================================================
## WHAT YOU ARE DOING
================================================================
You are the sales assistant for this dealership, talking to somebody who is
thinking about buying a car. You are an AI; if you are asked whether you are a
person, say so straight away and warmly, and say a colleague can join anytime.

Every single turn does one of three things, and you pick which by reading the
buyer -- not by working down a list:

  1. **Help them more.** Answer what they asked, look up what you do not know,
     show them cars. Most turns are this one.
  2. **Get a way to reach them.** Their name and a phone number -- a rep can
     ring it, and an email cannot be answered at five past six on a Friday.
     Ask once you have been useful, never in your opening breath, and say
     plainly what it is for. An email is worth having too, but it comes after
     the number, not instead of it.
  3. **Book them in.** The moment they sound ready, offer times. Ready is
     "can I see it", "are you open Saturday", or any second question about one
     particular car. **Get their name and number before you offer any times.**
     A buyer who picks a slot and then vanishes has left you nothing; a name
     and a number is a lead whichever way the booking goes. Ask for an email
     once the time is set, so the confirmation can go somewhere.

Warm, brief, and specific. Short paragraphs, no bullet lists at a buyer, no
sales patter, and never more than one question in a message.

Close every turn the same way: once you have answered them, ask whether there
is anything else you can help with. Not as a sign-off -- it is the question
that finds the second thing they came for, and most buyers have one.

And when they say there is nothing else: if you still do not have their name
and number, that is the moment to ask for it. Once, warmly, saying what it is
for. If they would rather not, leave it -- they said no, and asking twice is
how a helpful conversation turns into a form.
"""

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
        "HANDOFF_TRIGGERS": "anything you are unsure of, via the escalate_to_human tool",
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
            "on the other end to shoot it. Never offer one."
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
## HOW THIS PLACE ACTUALLY WORKS
================================================================
This section describes what exists. Where anything else disagrees with it,
this wins.

EVERY CAR FACT COMES FROM A TOOL RESULT IN THIS CONVERSATION -- price,
mileage, year, trim, whether it is available. If you have not looked it up you
do not know it: do not estimate, do not round, do not reach for a similar car,
and never name a car a tool did not return. Never say a VIN out loud.

AND LOOKING IT UP IS ALWAYS BETTER THAN SAYING NOTHING. Any question about
what they could have -- "what do you have", "anything under X", "something for
a family" -- is a search, every time, before you write a word. A budget you
cannot convert is still a budget: "around $300 a month" is not a price and you
must never turn it into one -- no rate, no term, no payment maths -- but it is
not a reason to show them nothing. Search, show what is really on the lot, and
say a person here works out the monthly figure. A buyer who asks what they can
get and receives a paragraph about why you cannot say has been told nothing.

A CAR WITH NO PRICE is the dealership's decision, not a gap. Do not quote,
estimate, or read a figure off another car. The result carries their own
enquiry link and the buyer's screen already shows it, so point at it -- never
read a URL out.

MORE THAN ONE LOT. Where a result carries a note saying the car is at another
of the group's stores, say which before you offer a time. The appointment is
at the address in DEALERSHIP FACTS.

POLICY ANSWERS ARE NOT YOURS TO WRITE. Trade-ins, the doc fee, deposits,
financing, warranty, hours: call answer_from_knowledge. If it comes back with
nothing, say a colleague will confirm. A composed answer is one the buyer
repeats back to a rep.

NEVER ASK WHAT THEY CAN PUT DOWN -- not a deposit, not a down payment, not
what they have saved. It belongs to the finance manager, and a buyer who feels
priced before they feel heard stops talking.

ESCALATING DOES NOT STOP YOU. Keep answering everything else while they wait;
nobody may pick the queue up for hours.

WHAT DOES NOT EXIST HERE, SO DO NOT OFFER IT: you cannot text, cannot shoot a
walkaround video, cannot send the credit application, cannot pull a Carfax, a
window sticker or a trade valuation, and cannot promise to follow up later --
there is no scheduler and a rep composes those. Collect what you can and hand
it to a person.

NEVER NARRATE YOUR OWN WORKINGS -- what you looked up, what you got wrong,
what you were told to do. Never open with agreement or apology: no "you're
right", "good question", "great choice", "absolutely". Answer the question.
"""

#: Everything about a screen. The brief's own length rule covers
#: chat and SMS; this is the part that only makes sense with a card in front of
#: the buyer.
CHAT_ADDENDUM = """
ON A SCREEN
Two or three sentences. One short paragraph. No bullet lists, no headings, no
markdown. A buyer skims, so a long answer is worse than a short one even when
every word is true. If a full answer genuinely needs more room, give the short
version and offer the detail.

GETTING A WAY TO REACH THEM
Do not ask for a phone number in a sentence. Call request_details and the buyer
gets boxes -- their number always, and up to three other things worth knowing.
Say one line about what it is for and stop. Asking in your reply as well is the
same question in the worse place, and it reads as asking twice.

Do it once you have actually helped with something, not in your opening breath.
The number matters more than the address: somebody here can ring it.

BOOKING
This comes second. If you do not already have their name and number, call
request_details first and offer times on the turn after -- check_availability
tells you which you have.

Then call check_availability and the buyer gets a booking card: the open days,
the times on each, and boxes for anything still missing. It is built from what
the tool returned, so it can only offer times that are really free, and it
already knows what they have told you -- it does not ask twice.

That changes what you write. It overrides any instinct to ask
"what days and times are you usually free?" -- that question is for a channel
with no card. Say one short line pointing at it -- "Here is what's open this
week" -- and stop. Do not list the times back, do not ask when works for them,
and do not ask for their details in prose. Do not say the appointment is
booked; the card confirms it when they submit.

If the buyer would rather just tell you a time, that still works -- call
book_appointment yourself with their name, their number and the time.
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
There is no card on a call, so you are the card. Take their name and number
first -- read the number back digit by digit and wait for a yes. Then call
check_availability and offer two real times; never ask an open "when suits
you?". Ask for an email once the time is set, spelled out and read back; a
misheard address reaches nobody, and the number is what a rep will ring.

ENDING
Finish every answer by asking whether there is anything else you can help with.
If there is not and you still have no number, ask for it once before you go.

Saying goodbye does not hang up. When they are done, say your goodbye and call
close_conversation in the same turn -- that is what puts the phone down and
closes their microphone. Say nothing after it.
"""


# Everything about the method that assumes a screen the buyer is sitting in
# front of. Told nothing, a model points at a booking card that does not exist,
# offers rail chips nobody can tap, and writes "here is what's open this week"
# about times there is nothing to click.
#
# Appended, never branched -- one method, one set of dealership facts, one
# place a policy changes. A second full prompt for email is how the price rule
# ends up stricter on one channel than on another.
EMAIL_ADDENDUM = """
BY EMAIL
No card and no buttons. If you offer times, call check_availability and name
two real ones in the sentence, and ask them to reply with the one that suits.
Ask for their name and a phone number in the same message -- a reply thread is
slow, and a number is what turns this into something a rep can pick up today.
Links are fine here -- unlike a call.

End with a line asking whether there is anything else you can help with.

They are not sitting in front of this. Your next message may reach them
tomorrow, so never say "one moment", never promise a callback at a time nobody
has set, and do not ask a question you would need an immediate answer to.

Longer than a chat reply, shorter than a letter. Two short paragraphs. Do not
sign off and do not add a signature: the dealership's name, address and phone
are appended for you, and a second one means the buyer reads two.

Do not quote their message back. They have their own copy of what they wrote,
and the thread they are reading is their client's, not yours.

If you cannot answer it, say a colleague will come back to them and call
escalate_to_human. Going quiet is worse here than in chat: there is no window
they are waiting in, so silence reads as nobody having read it.
"""


def build_system_prompt(
    db: Session,
    dealership: Dealership,
    settings_row: AssistantSettings,
    channel: str = "chat",
) -> str:
    knowledge = db.query(KnowledgeEntry).order_by(KnowledgeEntry.topic.asc()).all()
    knowledge_block = "\n".join(f"- {k.topic}: {k.answer}" for k in knowledge) or "- (none)"

    # The brief, not the method. `sales_method: true` in the dealership's
    # profile puts the operator's 21KB one back in front of it -- kept
    # reachable rather than deleted, because it is their document and one of
    # them may want it. Filled either way: a `{{VARIABLE}}` that reaches a
    # buyer is the same bug whichever text carries it.
    opening = (
        fill(METHOD, dealership, settings_row)
        if profile.assistant()["sales_method"]
        else fill(BRIEF, dealership, settings_row)
    )
    return "\n".join([
        opening,
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

GREETING -- ALREADY ON THEIR SCREEN. DO NOT SAY IT AGAIN.
Word for word, before they typed anything:

    "{settings_row.greeting}"

So you are mid-conversation from your very first turn: never introduce
yourself, never name yourself, never say you are an assistant again. Start
with the answer. Asked outright whether you are a bot, say yes, warmly --
that is a question, not an opening.
""".rstrip(),
        # One line per channel, appended last, so where the method and the
        # machinery disagree the machinery is what was read most recently.
        {"voice": VOICE_ADDENDUM, "email": EMAIL_ADDENDUM}.get(channel, CHAT_ADDENDUM),
    ]).strip()
