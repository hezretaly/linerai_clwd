"""How a list of cars is read back to a buyer, in one place.

The stub composed this and the deterministic rails needed the same thing, and
two versions of "here are three cars" is how one channel starts quoting a price
the other rounds. It is the same instinct as `app/recap.py`: text assembled
from rows, checkable against them, rather than written twice.

**Every number here comes out of a tool result.** That is what makes this
sayable at all -- the reply guard rejects a price that is not sourced, and a
templated sentence is sourced by construction. It still runs through the
guards, because a template that could slip an unsourced number past them would
be a hole in the guard rather than a licence.
"""

from __future__ import annotations

import re

#: Markdown a chat bubble renders as literal punctuation. `**$6,157**` reaches
#: the buyer with the asterisks in it, `### ` as three hashes, and a backtick
#: as a backtick -- the thread is plain text, deliberately: rendering markdown
#: in a bubble is a parser and a sanitiser on the one surface a stranger types
#: into, for emphasis nobody asked for. So the markers are removed instead.
#:
#: Underscores are left alone. `josh_r@example.com` is an address a buyer
#: typed, and `_` as emphasis is rare enough that dropping it would break more
#: than it tidied.
_MARKDOWN = (
    (re.compile(r"\*{1,3}"), ""),        # **bold**, *italic*, ***both***
    (re.compile(r"^ {0,3}#{1,6}\s+", re.M), ""),  # ### a heading
    (re.compile(r"`+"), ""),             # `code`
    (re.compile(r"\n{3,}"), "\n\n"),     # blank lines a bubble shows as gaps
)

#: A closing offer that is a second question. See `without_offer_more`.
_OFFER_MORE = re.compile(
    r"(?:^|(?<=[.!?\n]))[^.!?\n]*\banything else\b[^.!?\n]*\?\s*$",
    re.I,
)


def plain(text: str) -> str:
    """One reply, with markdown taken back out of it.

    A model writes markdown unless something stops it, and asking the prompt
    to stop is a request: the rule is here instead, on the one path every
    reply takes. It removes markers and never content -- no number, no make
    and no word of a sentence changes, which is what makes it safe to run
    after the guards have already read the text.
    """
    out = text or ""
    for pattern, repl in _MARKDOWN:
        out = pattern.sub(repl, out)
    return "\n".join(line.rstrip() for line in out.split("\n")).strip()


def asks_something_else(text: str) -> bool:
    """Is there a question in here *before* the closing offer?

    On a call there is no card to read, so this is what tells a double ask
    apart: two question marks, the last one being the sign-off. A real call ran
    on turns like *"Which one interests you most? And is there anything else I
    can help with?"* and *"Would you like more details on that one? And could I
    get your name and phone number?"* -- and a caller answers the last thing
    they heard, so the question that mattered went unanswered every time.
    """
    return "?" in _OFFER_MORE.sub("", text or "")


def without_offer_more(text: str) -> str:
    """Drop a trailing "is there anything else?" from a turn that is asking.

    **Two questions in one turn get the wrong one answered.** Every turn ends
    by offering more -- that is the behaviour `BRIEF` asks for, and it is how
    the second thing a buyer came for gets found. But a turn that has just put
    a card on their screen is already a question, and a real reply read:

        Please fill in the details on screen so a colleague can confirm the
        Versa's features. Is there anything else I can help with?

    The buyer is being asked for their number and asked to change the subject
    in the same breath. The card is the ask, so the sign-off goes.

    Only the final sentence, and only one that names "anything else" and ends
    in a question mark -- so the worst a false positive can do is remove a
    sentence that was redundant anyway.
    """
    return _OFFER_MORE.sub("", text or "").strip()


def money(value: int | None) -> str:
    """`$21,400`, or what the listing itself says when it carries no price.

    A missing price is a listing state the dealership chose, not a gap: 119 of
    Craig and Landreth's 486 cars are call-for-price. Saying so is the honest
    answer, and it is the only one the guards will pass -- there is no figure
    here to source.
    """
    return f"${value:,}" if value else "price on request"


def priced(value: int | None) -> str:
    """The same fact in a sentence: `is $21,400` / `is priced on request`.

    Two forms because the two read in different places, and both derive from
    the value rather than one wrapping the other -- there is no arrangement of
    "price on request" that reads as English after "The 2018 Challenger is".
    """
    return f"is ${value:,}" if value else "is priced on request"


#: The two makes the rule below gets wrong, and nothing else — `KIA` is short
#: enough to read as an initialism and is not one, `MINI` is long enough to
#: read as a word and the brand writes it in caps. `BMW`, `GMC` and `RAM` need
#: no entry: the length rule already leaves them alone. Curated for the reason
#: `ORIGIN_BY_MAKE` and `MAKE_NICKNAMES` are — there is no column for it, and
#: a list that restated what the rule already does is one more thing to keep
#: in step with it.
_CASED = {"KIA": "Kia", "MINI": "MINI"}

#: Short enough to be an initialism rather than a word. `BMW`, `GMC`, `SL`,
#: `XC`, `GT`, `RS` all stay as they are; `AUDI` and `CLASS` do not.
_INITIALISM = 3


def cased(value: str) -> str:
    """A SHOUTED name, in the case a person would write it.

    **A dealer's export is not a style guide.** Alsbou Motors' carries
    `AUDI`, `3 SERIES`, `MERCEDES-BENZ`, `3.0T QUATTRO PRESTIGE` — all caps,
    every row — while Craig and Landreth's crawl carries `Dodge` and
    `Challenger`. So one storefront shouted and the other did not, and Liner
    read `a 2018 AUDI Q7` out loud on a call.

    `str.title()` is not the fix and would be worse than the shouting:
    `XC60` becomes `Xc60` and `SL-CLASS` becomes `Sl-Class`, which are wrong
    names for real cars. So the rule is narrow — **only an all-caps token, and
    only one that is alphabetic and long enough not to be an initialism**:

        AUDI            -> Audi          3 SERIES   -> 3 Series
        MERCEDES-BENZ   -> Mercedes-Benz SL-CLASS   -> SL-Class
        XC60            -> XC60          330I       -> 330I
        BMW             -> BMW           3.0T       -> 3.0T

    Anything already mixed-case is returned untouched, so a crawled lot is not
    restyled and this only ever bites on an export that was shouting.

    It is **display only** — the row keeps what the dealer sent, and
    `search_inventory` lowercases before it matches, so nothing about what a
    buyer can find changes. And it is imperfect by design rather than by
    accident: `XDRIVE` comes back `Xdrive` where BMW writes `xDrive`. A trim
    is free text and there is no table of every one; a rule that guessed
    harder would invent names, which is the more expensive error.
    """
    if not value:
        return value
    # **A dealer decorates a field and a chat bubble renders it literally.**
    # Alsbou's Boxster is trimmed `CONVERTIBLE *LOW MILES*` in their own
    # export, and the asterisks reached the buyer as asterisks -- on the card,
    # in the sentence, and read out on a call. They are shouting, not data:
    # nothing downstream means anything by them, `search_inventory` lowercases
    # before it matches, and the row keeps what the dealer sent. Stripped
    # before the early return below, because a mixed-case trim can be
    # decorated too and `isupper()` would let that one through.
    if "*" in value:
        value = " ".join(value.replace("*", " ").split())
    if not value.isupper():
        return value
    if value in _CASED:
        return _CASED[value]
    out = []
    for run in value.split(" "):
        parts = run.split("-")
        out.append("-".join(
            p.capitalize() if p.isalpha() and len(p) > _INITIALISM else p
            for p in parts
        ))
    return " ".join(out)


def title(vehicle: dict) -> str:
    """`2020 Honda Accord Sport`, with no double spaces where a trim is blank."""
    parts = (vehicle.get("year"), vehicle.get("make"), vehicle.get("model"), vehicle.get("trim"))
    return " ".join(str(p) for p in parts if p).strip()


def one_line(vehicle: dict) -> str:
    """`2020 Honda Accord Sport -- $21,400, 38,120 miles`."""
    line = f"{title(vehicle)} -- {money(vehicle.get('price'))}"
    mileage = vehicle.get("mileage")
    if mileage:
        line += f", {mileage:,} miles"
    return line


def detail_line(vehicle: dict) -> str:
    """`The 2020 Honda Accord Sport is $21,400 with 38,120 miles.`

    Mileage is stated only where there is one. A dealer's own export is missing
    it on a couple of rows, and `f"{None:,}"` is a TypeError rather than a
    blank -- the stub crashed on the first such car rather than describing it.
    """
    line = f"The {title(vehicle)} {priced(vehicle.get('price'))}"
    mileage = vehicle.get("mileage")
    if mileage:
        line += f" with {mileage:,} miles"
    return line + "."


def pick_one(shown: list[dict]) -> tuple[dict, str]:
    """Which one to suggest, and the reason -- both read off the rows.

    A reason is only worth saying if it is true of the list in front of the
    buyer, so it is derived rather than asserted: cheapest, fewest miles, or
    both, and "the only one" when there is nothing to compare it against.
    """
    count = {2: "two", 3: "three"}.get(len(shown), str(len(shown)))
    cheapest = min(shown, key=lambda v: v.get("price") or 10**9)
    fewest = min(shown, key=lambda v: v.get("mileage") or 10**9)
    if len(shown) == 1:
        return shown[0], "the only one that fits right now"
    if cheapest["vin"] == fewest["vin"]:
        return cheapest, f"the cheapest of the {count} and the lowest mileage"
    return fewest, f"the lowest mileage of the {count}"


def describe_results(shown: list[dict], *, lead_in: str = "Here's what fits:") -> str:
    """The whole reply for a search that found something.

    `lead_in` is how the answer opens, and for a chip it restates what was
    asked -- "Here's what we have under $20,000" rather than "Here's what
    fits", because the buyer pressed a button and the answer should visibly be
    to *that* question.
    """
    lines = "\n".join(one_line(v) for v in shown)
    top, reason = pick_one(shown)
    return (
        f"{lead_in}\n{lines}\n\n"
        f"I'd start with the {top['make']} {top['model']} -- it's {reason}. "
        "Want the details on that one?"
    )


def nothing_found(what: str = "") -> str:
    """The honest answer when the lot has none.

    Names what was looked for, because "nothing matching that" after a chip
    press leaves the buyer unsure which of the two things they clicked came
    back empty.
    """
    subject = f" {what}" if what else " matching that"
    return (
        f"I don't have anything{subject} on the lot right now. Want me to widen the "
        "search, or tell me what matters most and I'll work from there?"
    )
