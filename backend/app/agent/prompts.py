"""System prompt assembly.

**One prompt a dealership may rewrite, then product code.** A manager edits
exactly one text on the Liner setup page, starting from `DEFAULT_PROMPT`: plain
English saying how Liner should treat their buyers and what matters most to
them. It is stored per settings version (`assistant_prompts.brief`, drafted
and published like everything else) and "" means ours. Everything else a model
is told is code and never reaches the page:

* who it is (`_opening`), one line ahead of the dealership's words;
* `OPERATING_RULES` -- how the tools behave and what no executor can
  enforce, every one of them there because something went wrong once;
* the dealership's facts, including the Behaviour tab's settings in words;
* one channel addendum: `CHAT_ADDENDUM`, `VOICE_ADDENDUM` or
  `EMAIL_ADDENDUM`;
* and, per turn, what `agent/priorities.py` adds when a buyer asks about
  paying or has just given their number.

It used to be six boxes -- a brief, the rules, one per channel and one for the
writing assistant -- and a manager who is not technical was being asked to edit
tool mechanics to change how Liner sounds. There is one assistant; it adapts to
the channel by itself, and that adaptation is ours to get right.

**A brief, not a script.** The default says the job in a few short
paragraphs and leaves the selling to a model that already knows how to sell.
It replaced `sales_method.md`, 21KB of NEPQ script that was two thirds of
every prompt this system sent -- a model handed two thirds of a script answers
like one: long, staged, and reluctant to just say what a car costs. The file
is **kept, not deleted** -- it is the operator's document -- and stays
reachable through `assistant: sales_method: true` in a dealership's profile,
where it becomes the default a manager starts from.

**One prompt with an addendum, never a prompt per channel** -- two is how the
price rule ends up stricter on one channel than another.

The writing assistant a rep presses Auto-generate or Polish on is not the
buyer-facing assistant and is not told what the manager wrote: it speaks as
the rep. `composer_system` is its whole instruction, and it is code too.
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

#: How hard to steer toward a visit. Worded to sit beside the owner's rule
#: that a number on file is the cue to book: these set the pace, never
#: whether. They reached only the archived method until the Behaviour tab was
#: stated in the facts block, so nothing that read them had noticed "after
#: every answered question" was a nag.
PUSH = {
    "gentle": "Offer a visit when it fits naturally, and do not repeat the offer.",
    "balanced": "Offer a visit once you have helped, and again when they sound ready.",
    "assertive": "Steer every turn toward a visit or a test drive, ending on a concrete next step.",
}

PRICE = {
    "listed_only": "Quote the listed price only. Never negotiate, never estimate a total.",
    "range_ok": "You may describe the listed price and note that a rep can discuss it.",
}

#: The financing posture. Neither sends a money question away: "hand off to a
#: rep" was the old wording, and it fought the owner's rule that any money
#: question gets the credit application.
FINANCING = {
    "refer_to_rep": "Leave rates, terms and approval to a person here; offer the application, never a figure.",
    "general_info": "You may explain how financing works in general terms, never with specific numbers.",
}

#: The operator's method, as supplied. Read once at import -- it is a file in
#: the image, not a row, so nothing can edit it at runtime and the setup page
#: offers the same bytes that were reviewed.
#:
#: **Off by default now, and that is a deliberate reversal.** It is 21KB of
#: NEPQ script -- about two thirds of every prompt this system sends -- and a
#: model given two thirds of a script answers like one: long, staged, and
#: reluctant to just say what a car costs. What replaced it is
#: `DEFAULT_PROMPT` below, which states the job in a few short paragraphs and
#: leaves the selling to a model that already knows how to sell. The file is
#: untouched and still reachable: a dealership that wants the long method sets
#: `assistant: sales_method: true` in its profile, which is where a fact about
#: the dealership belongs, and it becomes the default that dealership's
#: manager starts from (`default_prompt`).
METHOD = (Path(__file__).parent / "sales_method.md").read_text(encoding="utf-8")

#: **The one text a dealership edits**, and what it gets back on Reset.
#:
#: Written for the person who edits it: a sales manager, not an engineer. So
#: it is plain sentences about how to treat a buyer -- no tool names, no
#: placeholders, no headings -- and everything that is about *how the product
#: works* (the tools, the cards, what a call cannot do) is in the code that
#: follows it, where a rewrite cannot delete it. Who Liner is and which
#: dealership it is at is `_opening`, ahead of this, for the same reason.
#:
#: It keeps what the brief it replaced was for -- answer straight away, real
#: cars and real prices, one question at a time, nothing invented -- and it
#: states the owner's priorities in the owner's order: a name and a phone
#: number early; the credit application for any question about paying for
#: the car; and, once the number is in, a visit. The last two are also held by
#: code (`OPERATING_RULES`, `agent/priorities.py`), because a manager may
#: rewrite this and a prompt is a request.
#:
#: Short on purpose. It is re-read on every turn of every conversation, and on
#: the Riverside fixture its knowledge table leaves little room under
#: `PROMPT_MAX`: whatever this does not use is room for a manager's own words.
#: Its paragraphs are not hard-wrapped, unlike every other text here, because
#: this one is what the setup page's box shows -- and a box broken at column
#: 79 is one nobody can edit comfortably.
DEFAULT_PROMPT = """You help people who are thinking about buying a car from us. Be warm, brief and specific, like the best salesperson on the floor -- helpful, never pushy, never a script.

Answer what they ask, straight away. Show them real cars from our lot with their real prices. Never make up a car, a price or a fact: if you don't know, say someone here will find out.

Keep it short and ask one question at a time. Once you've answered, ask if there's anything else you can help with -- unless you've just asked them for something.

What matters most to us:
1. Get their name and phone number, so someone here can call them. Ask once you've helped with their first question, and say what it's for.
2. If they ask anything about credit, financing, a loan, a down payment, monthly payments, interest rates or getting approved -- bad credit included -- offer our credit application.
3. Once we have their number, help them book a visit or a test drive. When they sound ready ("can I see it?", "are you open Saturday?"), offer times.

If they'd rather not give their number, that's fine -- don't keep asking. If they're finishing up and we still don't have it, ask once more, warmly."""

#: The writing assistant's core -- what a rep gets when they press
#: Auto-generate or Polish on an email, a text or a chat reply. Short on
#: purpose: each draft's facts are composed after it (`email_draft.brief`),
#: and the shape a channel needs -- a subject line, a length -- is asked for in
#: the request itself (`loop.DRAFT_REQUESTS`). What kind of message it is
#: helping with is `COMPOSER_FOR`; `composer_system` puts the two together.
#: Product code: the manager's prompt is written to the buyer-facing assistant
#: and never reaches this one, which speaks as the rep.
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

#: What the writing assistant is helping with, by kind of message. Each is the
#: owner's own description of the job: an email is composed or polished from
#: the conversation so far and the email at hand; a chat reply is written for
#: a person who has taken the website chat over. A text has no framing of its
#: own -- the core and the request's shape (`loop.DRAFT_REQUESTS["sms"]`) are
#: the whole of it.
COMPOSER_FOR = {
    "email": """AN EMAIL
You are helping them compose, or polish, an email to this buyer -- a new one,
a reply to the email they have open, or the note on a forward. Work from the
conversation so far, THE EMAILS SO FAR with this buyer, what the brief says
about the buyer and the car, and the dealership's own answers -- and, where
there is one, THE EMAIL YOU ARE ANSWERING: respond to what it actually says.
Do not repeat what an earlier email already told them. Short paragraphs; a
buyer reads it on a phone.""",
    "chat": """A WEBSITE CHAT THEY HAVE TAKEN OVER
Liner was answering this buyer in the dealership's website chat, and a person
has now taken it over; the buyer can see it is somebody at the dealership.
Write their next reply to what the buyer last said, in the same thread. Do
not repeat what the chat already told them.""",
}


def composer_system(channel: str) -> str:
    """What the writing assistant is told, ahead of each draft's facts.

    No database and no dealership: nothing in it is the dealership's to edit,
    so a draft cannot pick up the manager's buyer-facing prompt -- which says
    "you are Liner" and would have a rep's own email speak as an assistant.
    `email_reply` and `email_forward` are emails; anything unknown gets the
    core alone.
    """
    kind = "email" if channel.startswith("email") else channel
    extra = COMPOSER_FOR.get(kind, "").strip()
    return f"{COMPOSER.strip()}\n\n{extra}" if extra else COMPOSER.strip()


#: Anything still wearing braces after the fill.
UNFILLED = re.compile(r"\{\{[^}]*\}\}")


def _hours_line(dealership: Dealership) -> str:
    return hours_sentence(json.loads(dealership.hours_json or "{}"))


_WEEK = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def hours_sentence(hours: dict) -> str:
    """"Open Monday-Saturday 10:00 to 20:00, Sunday 10:00 to 18:00."

    **Days are grouped only where their hours agree.** This used to read the
    first open day's window and print it for the whole run, so Alsbou -- who
    close at six on Sunday -- were told to the model as open until eight
    seven days a week, and a buyer asking about Sunday evening got a wrong
    yes from the one line meant to settle it. Bookable times were always
    right: `check_availability` reads `hours_json` itself.
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
    discount = _discount(row)
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


def _discount(row: AssistantSettings) -> str:
    """The discount a manager allowed, in words. One sentence for the archived
    method's `{{DISCOUNT_AUTHORITY}}` and the facts block alike."""
    return (
        f"you may go up to {row.discount_pct}% off the listed price"
        if row.discount_pct else
        "no discount authority -- never offer one"
    )


#: The ceiling on a dealership's own prompt, in characters. Liner's default is
#: about 1,100, so this is never what a manager meets first on a store with a
#: real knowledge table -- `prompt_room` is, since `PROMPT_MAX` is measured on
#: the whole assembled prompt. It is the cap where the rest is short.
OWN_PROMPT_MAX = 8000

#: Every `{{NAME}}` `fill` would look for. The page says nothing about them --
#: the dealership's name, hours and address are in the facts block already --
#: but a text pasted from somewhere may carry one, and anything `fill` cannot
#: answer would reach the model in braces, where a model eventually types it
#: to a buyer. So an unknown one is refused at save, in plain words.
PLACEHOLDER = re.compile(r"\{\{([A-Z_, ]+)\}\}")


#: The whole prompt an assistant is handed, per channel. `make agent-check`
#: pins the product's own under it on every channel, and a dealership's
#: prompt is refused at save if it would take any channel over -- every
#: character is re-read on every turn of every conversation, and it is the
#: cached prefix of the bill.
PROMPT_MAX = 12_000

#: The buyer-facing channels, each of which gets the manager's prompt.
CHANNELS = ("chat", "voice", "email")


def default_prompt() -> str:
    """The prompt a dealership starts from and gets back on Reset.

    The archived method where the profile asks for it, the default otherwise.
    Served to the page and compared on save from this one function, so the
    two cannot disagree -- they did: the page offered the method as the
    default while the save compared what it was sent with the brief.
    """
    return (METHOD if profile.assistant()["sales_method"] else DEFAULT_PROMPT).strip()


def own_prompt(db: Session, settings_row: AssistantSettings | None) -> str:
    """This settings version's own prompt, "" where it uses Liner's.

    `assistant_prompts.brief` is the column, because it already held exactly
    this: the text at the top of every buyer-facing prompt, "" meaning ours. A
    row saved before there was one prompt reads the same way -- its brief was
    followed by the product's rules then, and the rules are still appended
    now. `rules` and `assistant_parts` are not read: the rules and each
    channel's instructions are product code.
    """
    from app.models import AssistantPrompt

    if settings_row is None or not settings_row.id:
        return ""
    row = db.query(AssistantPrompt).filter_by(settings_id=settings_row.id).one_or_none()
    return (row.brief or "").strip() if row else ""


def prompt_lengths(db: Session, dealership: Dealership, settings_row: AssistantSettings) -> dict:
    """How long each buyer-facing assistant's whole prompt comes out."""
    return {
        channel: len(build_system_prompt(db, dealership, settings_row, channel))
        for channel in CHANNELS
    }


def prompt_room(db: Session, dealership: Dealership, settings_row: AssistantSettings) -> int:
    """The most the dealership's prompt can be, in characters, right now.

    The smaller of `OWN_PROMPT_MAX` and what `PROMPT_MAX` leaves once the
    longest channel has added everything that is not the manager's: who Liner
    is, the rules, the facts, the knowledge table and its own addendum. One
    number, because the page has one box. It moves when the knowledge table
    does, and the save measures the real thing either way.
    """
    overhead = max(
        len(before) + len(after)
        for before, after in (
            _around(db, dealership, settings_row, channel) for channel in CHANNELS
        )
    )
    return max(0, min(OWN_PROMPT_MAX, PROMPT_MAX - overhead))


def unknown_placeholders(text: str, dealership: Dealership, row: AssistantSettings) -> list[str]:
    """The `{{NAME}}`s in `text` that `fill` would leave in braces."""
    known = set(_variables(dealership, row))
    return sorted({m for m in PLACEHOLDER.findall(text or "") if m not in known})


def fill(text: str, dealership: Dealership, row: AssistantSettings) -> str:
    values = _variables(dealership, row)
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


#: What the dealership's prompt cannot know: that there are tools, that a chat
#: buyer is looking at a screen, and that some answers come out of a table
#: rather than out of the model. Appended after it, so where the two collide
#: this is the last thing read -- and product code, never on the setup page, so
#: a manager rewriting how Liner sounds cannot delete what keeps it honest.
#:
#: Two of the owner's priorities are here as well as in the default prompt, so
#: they hold on a call and under a manager's own words: a money question gets
#: the credit application, and a number on file is the cue to book. Chat and
#: email are also told on the turn itself (`agent/priorities.py`); a call has
#: no per-turn hook, so for a call this is where they live.
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
say a person here works out the monthly figure.

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

MONEY QUESTIONS GET THE APPLICATION. Credit, financing, a loan, a down
payment, a monthly payment, a rate, getting approved, bad credit: call
offer_credit_application in that same turn -- applying is the next step
whatever the numbers turn out to be. Never work out a rate, a payment or an
approval yourself; a person here does.

A PERSON'S NUMBER. Out-the-door price, a better price, a trade value: say a
colleague works it out and call escalate_to_human -- that brings up the
contact form when you have no number.

ONCE YOU CAN REACH THEM, BOOK THEM. With their name and number on file and
nothing booked, the next step is a visit or a test drive: offer times with
check_availability, as often as the Booking line below allows.

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
priced before they feel heard stops talking. If they raise it, it is a money
question, above.

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


#: What each channel adds, appended last so where the dealership's words and
#: the machinery disagree the machinery is what was read most recently.
#: Product code: never served to the setup page and never the dealership's to
#: rewrite -- a call needing to be told it has no screen is not a matter of
#: taste. Anything that is not a call or an inbox is a screen.
CHANNEL_ADDENDA = {"chat": CHAT_ADDENDUM, "voice": VOICE_ADDENDUM, "email": EMAIL_ADDENDUM}


def channel_addendum(channel: str) -> str:
    return CHANNEL_ADDENDA.get(channel, CHAT_ADDENDUM).strip()


def _opening(dealership: Dealership) -> str:
    """Who is talking, ahead of the dealership's own words.

    Code rather than the first line of the editable text: a manager rewriting
    how Liner treats their buyers should not have to know to say which
    dealership it is at, and a rewrite that dropped it left the model not
    knowing. Being an AI, and saying so when asked, is in the facts below.
    """
    return (
        "WHAT YOU ARE DOING\n"
        f"You are Liner, the sales assistant at {dealership.name}, talking with "
        "somebody who is thinking about buying a car."
    )


def _manner(row: AssistantSettings) -> str:
    """The Behaviour tab, in words the model reads.

    **These reached nothing on the default prompt.** Tone, push level,
    financing posture and discount authority were read only through
    placeholders in the archived method, so a manager changing the tone on
    the setup page changed the writing assistant and not one word a buyer got
    -- a control that did nothing a buyer could see. Stated here, in the facts
    every channel carries, whatever the dealership's own prompt says.
    """
    return "\n".join([
        "HOW THIS DEALERSHIP WANTS IT DONE",
        f"Tone: {TONE.get(row.tone, TONE['warm'])}",
        f"Booking: {PUSH.get(row.push_level, PUSH['balanced'])}",
        f"Price: {PRICE.get(row.price_mode, PRICE['listed_only'])}",
        f"Financing: {FINANCING.get(row.financing_mode, FINANCING['refer_to_rep'])}",
        f"Discounts: {_discount(row)}.",
    ])


def _around(
    db: Session, dealership: Dealership, settings_row: AssistantSettings, channel: str
) -> tuple[str, str]:
    """Everything a channel's prompt carries that is not the dealership's
    prompt: what goes before it and what goes after. `prompt_room` measures
    these, so the number the page shows is exact rather than estimated."""
    knowledge = db.query(KnowledgeEntry).order_by(KnowledgeEntry.topic.asc()).all()
    knowledge_block = "\n".join(f"- {k.topic}: {k.answer}" for k in knowledge) or "- (none)"
    facts = f"""
DEALERSHIP FACTS
{dealership.name}, {dealership.address}. Phone {dealership.phone}.
{_hours_line(dealership)} Timezone {dealership.timezone}.
Appointment slots are {settings_row.booking_slot_length} minutes.

{_manner(settings_row)}

WHAT YOU KNOW BEYOND THE LISTINGS
{knowledge_block}

GREETING -- ALREADY ON THEIR SCREEN. DO NOT SAY IT AGAIN.
Word for word, before they typed anything:

    "{settings_row.greeting}"

So you are mid-conversation from your very first turn: never introduce
yourself, never name yourself, never say you are an assistant again. Start
with the answer. Asked outright whether you are a bot, say yes, warmly, and
that a colleague can join any time -- that is a question, not an opening.
""".rstrip()
    before = f"{_opening(dealership)}\n\n"
    after = "\n" + "\n".join([
        OPERATING_RULES.rstrip(),
        facts,
        "",
        channel_addendum(channel),
    ])
    return before, after


def build_system_prompt(
    db: Session,
    dealership: Dealership,
    settings_row: AssistantSettings,
    channel: str = "chat",
) -> str:
    """One channel's whole prompt: who Liner is, the dealership's prompt (or
    ours), then the rules, the facts and the channel's addendum -- code.

    Filled either way: a `{{VARIABLE}}` that reaches a buyer is the same bug
    whichever text carries it, and the archived method is full of them.
    """
    text = fill(own_prompt(db, settings_row) or default_prompt(), dealership, settings_row).strip()
    before, after = _around(db, dealership, settings_row, channel)
    return f"{before}{text}{after}"
