"""The details card: asking a buyer for something in boxes instead of prose.

A chat can only ask one question at a time and has to parse the answer back
out of a sentence. That is where things get lost -- "sam, 502 555 0134 or the
other one" is a real shape of reply -- and it is slow: four facts is four
round trips, each one a chance for the buyer to stop answering.

**The same pattern as the booking card, deliberately.** The card is built from
a tool result and can only offer what that result named; its submit goes
through the same executors any other caller uses. It is not a second way to
write a lead.

**And it is the honest answer to a problem the rail chips could not solve.** A
chip cannot ask for a name or a phone number -- its `message_text` is sent as
the buyer's own words, so a pre-written one would put words in their mouth,
which is how a real person once told Liner they were a fixture buyer called
Jordan Reyes. A form has no such problem: the buyer types into it, so
`typed` provenance is not just defensible here, it is more literally true than
anywhere else in the system.

**The vocabulary is closed, and that is the point of this module.** `key` on
`CapturedField` is a free string, and it had already drifted -- this database
holds both `timeframe` and `timeline` rows meaning the same thing, written by
the same model on different days. A rep reading a buyer page cannot tell those
apart from a real distinction. The card offers these keys and no others.
"""

from __future__ import annotations

from dataclasses import dataclass

#: How the buyer's phone number is stored. Named once because two places write
#: it and `app/matching.py` reads it as an identity rung.
PHONE_KEY = "phone"


@dataclass(frozen=True)
class Field:
    """One box on the card."""

    key: str
    label: str
    #: What the browser should show: a phone keypad on a phone, an email
    #: keyboard for an address. `choice` renders as buttons, not a select --
    #: three taps beats a dropdown on a 390px screen.
    kind: str = "text"
    placeholder: str = ""
    choices: tuple[str, ...] = ()
    #: Shown under the box. Only where the question is not self-explanatory:
    #: a hint under every field is a wall of text nobody reads.
    hint: str = ""


#: Everything the card may ask for. Contact first, because that is what this
#: exists to get -- a number a rep can ring is the whole objective the brief
#: names second, and it is the one thing a conversation cannot recover once the
#: buyer closes the tab.
FIELDS: dict[str, Field] = {
    "name": Field("name", "Your name", placeholder="First name is fine"),
    PHONE_KEY: Field(
        PHONE_KEY, "Phone number", kind="tel", placeholder="(502) 555-0134",
        hint="So someone here can call you back about it.",
    ),
    "email": Field(
        "email", "Email", kind="email", placeholder="you@example.com",
        hint="Optional. Only if you would rather we wrote.",
    ),
    "budget": Field(
        "budget", "Budget", placeholder="e.g. under $25,000, or $350 a month",
    ),
    "timeframe": Field(
        "timeframe", "When are you looking to buy?", kind="choice",
        choices=("This week", "This month", "In a few months", "Just looking"),
    ),
    "trade_in": Field(
        "trade_in", "Anything to trade in?", placeholder="Year, make and model",
    ),
    "financing": Field(
        "financing", "Paying cash or financing?", kind="choice",
        choices=("Financing", "Cash", "Not sure yet"),
    ),
    "use_case": Field(
        "use_case", "What do you need it for?",
        placeholder="e.g. school run, towing, a long commute",
    ),
}

#: Asked for unless the model names something else. A number and who it belongs
#: to is the minimum that makes a lead worth a rep's time.
DEFAULT_KEYS = ("name", PHONE_KEY)

#: The card refuses to render without this one. The operator's rule: a phone
#: number is the thing to get, because a rep can ring it -- an email cannot be
#: answered at five past six on a Friday.
REQUIRED_KEYS = (PHONE_KEY,)

#: More than this on one card is a form, and a form in a chat window is a
#: wall. Four boxes is the most somebody fills in without deciding not to.
MAX_FIELDS = 4


def wanted(keys) -> list[str]:
    """The keys to show, cleaned up. Unknown ones are dropped, not guessed at.

    A model asking for `phone_number` gets nothing rather than a ninth key in
    the vocabulary this module exists to keep closed. The phone is put back if
    it was left out: every card is allowed to ask for other things, and none
    of them is allowed to skip the one that makes the lead workable.
    """
    seen: list[str] = []
    for raw in keys or ():
        key = str(raw or "").strip().lower()
        if key in FIELDS and key not in seen:
            seen.append(key)
    if not seen:
        seen = list(DEFAULT_KEYS)
    for required in REQUIRED_KEYS:
        if required not in seen:
            seen.append(required)
    return seen[:MAX_FIELDS]


def card(keys, reason: str = "") -> dict:
    """The card spec a tool result carries, and the browser renders verbatim.

    `reason` is the model's own one line saying what the details are for, shown
    above the boxes. Asking for a phone number with no stated purpose is the
    moment a buyer decides this is a lead-capture form rather than help.
    """
    shown = wanted(keys)
    return {
        "reason": (reason or "").strip(),
        "required": [k for k in REQUIRED_KEYS if k in shown],
        "fields": [
            {
                "key": f.key, "label": f.label, "kind": f.kind,
                "placeholder": f.placeholder, "choices": list(f.choices),
                "hint": f.hint, "required": f.key in REQUIRED_KEYS,
            }
            for f in (FIELDS[k] for k in shown)
        ],
    }


def readable(values: dict) -> str:
    """What the buyer submitted, as a sentence in their own voice.

    Written into the transcript as their message, exactly as the booking card
    does. Three jobs at once: the thread stays readable for a rep, later turns
    can see the details without a second tool call, and it is what
    `save_captured_fields` checks a value against before it will accept
    `typed` rather than downgrading it to a guess. A form submission really is
    the buyer's own words -- they typed them into a labelled box -- and this is
    what makes the provenance check agree.
    """
    parts = []
    for key, value in values.items():
        text = str(value or "").strip()
        if not text or key not in FIELDS:
            continue
        parts.append(f"{FIELDS[key].label}: {text}")
    return " · ".join(parts)
