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
#: The objectives are the operator's own words and they are written as a
#: choice rather than a sequence, because a script that must be walked in order
#: is what the 21KB one was. Every turn does one of them; which one is the
#: model reading the buyer, which is the thing being paid for. The fourth --
#: the finance application -- is theirs too: booking first, the application
#: next, and a way to reach them for anything only a person can answer.
BRIEF = """
================================================================
## WHAT YOU ARE DOING
================================================================
You are the sales assistant for this dealership, talking to somebody who is
thinking about buying a car. You are an AI; if you are asked whether you are a
person, say so straight away and warmly, and say a colleague can join anytime.

Every single turn does one of these things, and you pick which by reading the
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
  4. **Start their finance application.** When payments, credit or approval
     come up, call offer_credit_application.

Warm, brief, and specific. Short paragraphs, no bullet lists at a buyer, no
sales patter, and never more than one question in a message.

Close every turn the same way: once you have answered them, ask whether there
is anything else you can help with. Not as a sign-off -- it is the question
that finds the second thing they came for, and most buyers have one. But not
in a turn that is itself asking them for something: boxes on their screen are
already the question, and adding "anything else?" under them asks them to fill
it in and to change the subject in the same breath.

And when they say there is nothing else: if you still do not have their name
and number, that is the moment to ask for it. Once, warmly, saying what it is
for. If they would rather not, leave it -- they said no, and asking twice is
how a helpful conversation turns into a form.
"""

#: The writing assistant's instructions -- what a rep gets when they press
#: Auto-generate or Polish on an email, a text or a chat reply. Short on
#: purpose: each draft's facts are composed after it (`email_draft.brief`),
#: and the shape a channel needs -- a subject line, a length -- is asked for in
#: the request itself (`loop.DRAFT_REQUESTS`), so a dealership rewriting this
#: cannot lose the part that makes an email an email.
COMPOSER = """You write messages for a member of a car dealership's sales team -- emails,
texts and replies in a website chat. They read what you write, may edit it,
and send it themselves under their own name. It is their message, not an
assistant's.

Write in the first person as the person named under WHO IS WRITING: "I" for
them and "we" for the dealership. Never mention Liner, an assistant or AI.
Anything that needs checking, they check: "I'll confirm that and come back to
you", never "a colleague will". Anything that happens at the dealership, they
are part of: "when you come in, I can go through the price with you", never
"someone can".

State only facts written in the brief below -- no price, mileage, feature,
vehicle or policy that is not there. Plain text, no markdown."""

#: Anything still wearing braces after the fill.
UNFILLED = re.compile(r"\{\{[^}]*\}\}")


def _hours_line(dealership: Dealership) -> str:
    return hours_sentence(json.loads(dealership.hours_json or "{}"))


_WEEK = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def hours_sentence(hours: dict) -> str:
    """"Open Monday-Thursday 09:00 to 20:00, Friday-Saturday 09:00 to 19:00."

    **Days are grouped only where their hours agree.** This used to read the
    first open day's window and print it for the whole run, so Craig and
    Landreth -- who close at seven on Friday and Saturday, and whose profile
    says why that is worth keeping exact -- were told to the model as open
    until eight all week, and a buyer asking about Saturday evening got the
    wrong answer from the one line meant to settle it.
    """
    runs: list[list] = []
    for n, day in enumerate(_WEEK):
        window = hours.get(day)
        if not window:
            continue
        key = (window["open"], window["close"])
        if runs and runs[-1][2] == key and runs[-1][3] == n - 1:
            runs[-1][1], runs[-1][3] = day, n
        else:
            runs.append([day, day, key, n])
    if not runs:
        return "Hours are not configured."
    line = "Open " + ", ".join(
        f"{first.title()}{'' if first == last else '-' + last.title()} {opens} to {closes}"
        for first, last, (opens, closes), _ in runs
    ) + "."
    closed = [day.title() for day in _WEEK if day in hours and not hours[day]]
    if closed:
        line += f" Closed {', '.join(closed)}."
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
        # The application is a tool now, and the URL stays out of the prompt:
        # in chat the tool draws a counted button, and a model never shown the
        # address cannot mistype it into a sentence.
        "CREDIT_APP_LINK": (
            "the dealership's credit application -- call offer_credit_application and "
            "it goes on their screen"
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


#: The ceiling on a dealership's own brief and rules together. The product's
#: pair is about 6,700 characters and `make agent-check` fails the whole prompt
#: past 12,000; this leaves room for the facts, the knowledge table and the
#: channel addendum that follow, so an edit cannot push every turn of every
#: conversation over the line the gate exists to hold.
OWN_PROMPT_MAX = 8000

#: Every `{{NAME}}` a dealership may use in its own wording -- the ones `fill`
#: answers. Anything else would reach the model in braces, and a model handed
#: one eventually types it to a buyer.
PLACEHOLDER = re.compile(r"\{\{([A-Z_, ]+)\}\}")


#: The whole prompt an assistant is handed, per channel. `make agent-check`
#: pins the product's own under it, and a dealership's wording is refused at
#: save if it would take any channel over -- every character is re-read on
#: every turn of every conversation, and it is the cached prefix of the bill.
PROMPT_MAX = 12_000

#: Each assistant's own instructions, on top of the shared brief and rules.
#: Closed: `AssistantPart.part` is one of these or it is refused.
PARTS = ("chat", "voice", "email", "composer")

#: How long each may be. The call's is the gate's 1,500 -- it is re-read on
#: every turn of a call that bills by the minute -- and the rest leave room for
#: the facts and the knowledge table inside `PROMPT_MAX`.
PART_MAX = {"chat": 3000, "voice": 1500, "email": 2000, "composer": 2500}


def own_prompt(db: Session, settings_row: AssistantSettings) -> dict:
    """This settings version's own wording, "" wherever it uses ours.

    The brief and rules every buyer-facing assistant shares, and one part per
    assistant (`PARTS`). One dict for all six, because `unpublished` compares
    live with draft through it and a part it did not return would be an edit
    the banner never mentioned.
    """
    from app.models import AssistantPart, AssistantPrompt

    have = settings_row is not None and settings_row.id
    row = (
        db.query(AssistantPrompt).filter_by(settings_id=settings_row.id).one_or_none()
        if have else None
    )
    out = {
        "brief": (row.brief or "").strip() if row else "",
        "rules": (row.rules or "").strip() if row else "",
        **{part: "" for part in PARTS},
    }
    if have:
        for p in db.query(AssistantPart).filter_by(settings_id=settings_row.id).all():
            if p.part in PARTS:
                out[p.part] = (p.text or "").strip()
    return out


def default_part(part: str) -> str:
    """The product's own text for one assistant's instructions."""
    return {
        "chat": CHAT_ADDENDUM, "voice": VOICE_ADDENDUM,
        "email": EMAIL_ADDENDUM, "composer": COMPOSER,
    }[part].strip()


def composer_system(db: Session, dealership: Dealership, settings_row: AssistantSettings) -> str:
    """What the writing assistant is told it is, ahead of each draft's facts."""
    own = own_prompt(db, settings_row)["composer"]
    return fill(own, dealership, settings_row) if own else COMPOSER.strip()


def prompt_lengths(db: Session, dealership: Dealership, settings_row: AssistantSettings) -> dict:
    """How long each buyer-facing assistant's whole prompt comes out."""
    return {
        channel: len(build_system_prompt(db, dealership, settings_row, channel))
        for channel in ("chat", "voice", "email")
    }


def unknown_placeholders(text: str, dealership: Dealership, row: AssistantSettings) -> list[str]:
    """The `{{NAME}}`s in `text` that `fill` would leave in braces."""
    known = set(_variables(dealership, row))
    return sorted({m for m in PLACEHOLDER.findall(text or "") if m not in known})


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

EQUIPMENT IS IN THE CAR'S OPTIONS LIST. Seats, rows, sunroof, tow package,
drivetrain, heated seats: get_vehicle returns the listing's own options, so
call it for the car in question before you answer. If the list names it,
answer from it. If the list is there and does not, or the car has none, the
record cannot answer -- treat it exactly as below. Never reason it out from
what you know about that model in general: this is one specific car.

A CAR'S HISTORY IS YOURS TO GIVE. Where get_vehicle returns a `detail` -- the
dealership's own write-up of that car's history report: owners, accidents,
service, recalls -- give it whenever they ask, in as much detail as they ask.
Never say you cannot retrieve, access or provide it; call get_vehicle again if
it is no longer in view. A question it answers is answered, never handed to a
colleague, even while one is on the way about something else.

A CARFAX IS A LINK. Where a car carries `history_url`, that is its history
report: for a Carfax, accident or owner question, call get_vehicle and point
at the report link on its card (by email, give the link; on a call, it is on
the car's page on the website). Never escalate that or say you cannot.

A PERSON'S NUMBER. Out-the-door price, a better price, a trade value, a
monthly payment: say a colleague works it out and call escalate_to_human --
that brings up the contact form when you have no number.

A QUESTION THE RECORD CANNOT ANSWER is a lead, not a dead end. In the same
turn: say once, in one sentence, that a colleague will confirm it, and call
escalate_to_human with the question. That call puts the contact form on
their screen by itself when we have no way to reach them, so say what it is
for in one line and do not ask for anything else in that message. Never promise a colleague will get back to them and leave the turn
without that call -- nobody can, and a refusal that asks for nothing is the
whole conversation wasted. If they press the point, do not say again that the
record does not show it: they heard you. Never restate the refusal.

NEVER ASK WHAT THEY CAN PUT DOWN -- not a deposit, not a down payment, not
what they have saved. It belongs to the finance manager, and a buyer who feels
priced before they feel heard stops talking.

ESCALATING DOES NOT STOP YOU. Keep answering everything else while they wait;
nobody may pick the queue up for hours.

WHAT YOU CANNOT DO, SO DO NOT OFFER IT: you cannot text -- a colleague here
can, and you have no way to send one or to promise they will -- cannot shoot a
walkaround video, cannot produce a Carfax, a window sticker or a trade
valuation for a car whose record carries none, and
cannot promise to follow up later --
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
gets the contact form -- their number and their email always, plus anything
else worth knowing. Call it the contact form, never "details". Say one line
about what it is for and stop. Asking in your reply as well is the same
question in the worse place, and it reads as asking twice. It stays where it
was drawn; to point them back at it, call request_details again -- that
brings it down under your reply.

Do it once you have actually helped with something, not in your opening breath.
The number is the one they have to fill in, because somebody here can ring it;
the email is there and optional, for a buyer who would rather be written to.

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
first -- read the number back digit by digit, wait for a yes, then save it with
save_captured_fields before you do anything else. Until you save it nothing here
knows you have it and you will be asked for it again. Then check_availability
and offer two real times; never an open "when suits you?". Ask for an email once
the time is set, spelled out and read back.

ENDING
One question per turn, and never two -- they answer the last one and the other
is lost. "Anything else I can help with?" is a turn of its own, once you have
answered them, not a tail on another question. If they are done and you still
have no number, ask for it once before you go.

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


def _lots_line(db: Session) -> str:
    """The group's other lots, for a group; nothing for a dealership with one.

    One line, because every car's own result already says which lot it is on
    and where a visit to see it is booked; this is for "where are you?" and
    "do you have a store in Clarksville?". A lot with no street address on
    file says so, so the answer to "where is it?" is the number, not a guess.
    """
    from app import locations

    lots = locations.Lots(db)
    if not lots.several or lots.primary is None:
        return ""
    others = [lot for lot in lots.active if lot is not lots.primary]
    listed = ", ".join(
        f"{lot.name} ({'; '.join(b for b in (lot.address or 'no street address on file', lot.phone, hours_sentence(locations.hours(lot)) if locations.hours(lot) else '') if b)})"
        for lot in others
    )
    return (f"\nThat is our {lots.primary.name} store. Our other lots: {listed}. "
            "Each car's result says which lot it is on.")


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
    custom = own_prompt(db, settings_row)
    opening = (
        fill(custom["brief"], dealership, settings_row) if custom["brief"]
        else fill(METHOD, dealership, settings_row)
        if profile.assistant()["sales_method"]
        else fill(BRIEF, dealership, settings_row)
    )
    return "\n".join([
        opening,
        fill(custom["rules"], dealership, settings_row) if custom["rules"] else OPERATING_RULES,
        f"""
DEALERSHIP FACTS
{dealership.name}, {dealership.address}. Phone {dealership.phone}.{_lots_line(db)}
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
        # One part per channel, appended last, so where the method and the
        # machinery disagree the machinery is what was read most recently. A
        # dealership's own wording for that assistant replaces ours whole.
        _channel_part(custom, channel, dealership, settings_row),
    ]).strip()


def _channel_part(custom: dict, channel: str, dealership: Dealership, row: AssistantSettings) -> str:
    part = channel if channel in ("voice", "email") else "chat"
    return fill(custom[part], dealership, row) if custom[part] else default_part(part)
