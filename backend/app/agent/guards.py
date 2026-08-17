"""Response guards. Run before any assistant text is released to a buyer.

The unsourced-fact guard is the single most important piece of code here: any
price, mileage or availability claim in the assistant's text must appear in a
tool result from this same turn. If the model cannot source it, the buyer does
not see it.

These run in *every* LLM_MODE, stub included. If a stubbed turn could slip an
unsourced price through, the guard has a hole and that should surface offline
rather than in front of a prospect.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")
MILEAGE_RE = re.compile(r"([\d,]{3,})\s*(?:miles|mi\b|k miles)", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
AVAILABILITY_RE = re.compile(
    r"\b(in stock|on the lot|still available|we have (?:one|it|a)|available now)\b",
    re.IGNORECASE,
)

# A buyer restating their own constraint is not a claim about a car: "under
# $20,000" is the question, not an offer. Numbers in these positions are checked
# against what the buyer said and what was searched for, not only against what
# came back.
BOUND_RE = re.compile(
    r"(?:under|below|beneath|less than|lower than|up to|at most|no more than|within|"
    r"around|about|roughly|near|over|above|more than|at least|between|budget|"
    r"max(?:imum)?|min(?:imum)?|cap|range of|from)\W{0,4}$",
    re.IGNORECASE,
)

# What Liner says when it has to stop and get a person. Rotated by turn so two
# in a row never read as the same canned line -- the tell that made a guard
# misfire look like a broken bot. Each one asks for a contact, because an
# escalation nobody can follow up on is just a dead end.
SAFE_FALLBACKS = (
    "Let me have someone here check that one for you. What's the best number or "
    "email to reach you at?",
    "I'd like a person on our team to confirm that before I answer. Can I get "
    "your name and the best way to reach you?",
    "Let me pull someone in on that one. What's a good number or email for you?",
)


def safe_fallback(seed: int = 0) -> str:
    return SAFE_FALLBACKS[seed % len(SAFE_FALLBACKS)]

VOICE_MAX_WORDS = 45


@dataclass
class GuardResult:
    ok: bool
    text: str
    violations: list[str] = field(default_factory=list)
    should_escalate: bool = False
    nudge: str = ""


def _numbers_in(value: Any, into: set[str]) -> None:
    """Collect every number appearing anywhere in a tool result, normalised."""
    if isinstance(value, dict):
        for v in value.values():
            _numbers_in(v, into)
    elif isinstance(value, list):
        for v in value:
            _numbers_in(v, into)
    elif isinstance(value, (int, float)):
        into.add(str(int(value)))
    elif isinstance(value, str):
        for match in re.findall(r"\d[\d,]*", value):
            into.add(match.replace(",", ""))


def sourced_numbers(tool_results: list[dict]) -> set[str]:
    grounded: set[str] = set()
    for result in tool_results:
        _numbers_in(result, grounded)
    # A price of 21400 is legitimately written "$21,400" or "21.4k"; normalise
    # the common roundings so the guard flags invention, not formatting.
    for number in list(grounded):
        if number.isdigit() and len(number) >= 4:
            grounded.add(number[:-3] + "k")
            grounded.add(str(round(int(number) / 1000)))
    return grounded


def check_unsourced_facts(
    text: str,
    tool_results: list[dict],
    *,
    tool_inputs: list[dict] | None = None,
    buyer_text: str = "",
) -> list[str]:
    """Which numbers in ``text`` came from nowhere.

    Three sources count as grounding, and leaving any of them out is what made
    this guard reject every budget question:

    * **Tool results** -- the price and mileage of a real car. Always grounding.
    * **Tool inputs** -- ``search_inventory(max_price=20000)``. Describing the
      search it just ran is not the same as inventing a price.
    * **What the buyer said**, but only where the text is restating it as a
      bound ("under $20,000"). The buyer typed that number and can see it on
      screen. Confirming a price the buyer *guessed* at a specific car still
      has to come from inventory, which is why the bound test is there.
    """
    grounded = sourced_numbers(tool_results)
    grounded |= sourced_numbers(list(tool_inputs or []))
    asked = sourced_numbers([buyer_text]) if buyer_text else set()
    violations: list[str] = []

    def bounded(at: int) -> bool:
        return bool(BOUND_RE.search(text[:at]))

    for match in MONEY_RE.finditer(text):
        raw = match.group(1)
        value = raw.replace(",", "").split(".")[0]
        if value in grounded or (value in asked and bounded(match.start())):
            continue
        violations.append(f"unsourced price ${raw}")

    for match in MILEAGE_RE.finditer(text):
        raw = match.group(1)
        value = raw.replace(",", "")
        if value in grounded or (value in asked and bounded(match.start())):
            continue
        violations.append(f"unsourced mileage {raw}")

    for raw in YEAR_RE.findall(text):
        # A model year the buyer named is theirs, wherever it appears -- "no,
        # nothing from 2019 right now" is an honest answer, not a claim.
        if raw not in grounded and raw not in asked:
            violations.append(f"unsourced model year {raw}")

    if AVAILABILITY_RE.search(text) and not tool_results:
        violations.append("availability claim with no inventory lookup this turn")

    return violations


# Makes that are also ordinary words. "I won't dodge that" and "a mini SUV"
# are honest sentences, and a guard that rejects them costs the buyer their
# answer -- which is a worse failure than the one being guarded against. Ram
# is dropped with Dodge for the same reason and loses nothing: it is a Dodge
# sub-brand, so the make it ships under is still watched.
AMBIGUOUS_MAKES = {"dodge", "ram", "mini", "mg", "smart"}


def check_unsourced_vehicles(
    text: str,
    tool_results: list[dict],
    *,
    makes: set[str],
    tool_inputs: list[dict] | None = None,
    buyer_text: str = "",
    prior_results: list[dict] | None = None,
) -> list[str]:
    """Which cars in ``text`` this dealership never looked up.

    The executor is the real guarantee -- ``search_inventory`` only ever
    returns rows that are ``available`` and discussable, so a sold car cannot
    come back through a tool. What it cannot stop is the model *naming* one
    anyway, out of what it knows about cars in general rather than about this
    lot. "We've got a Ford Escape too" carries no price, no mileage and no year,
    so every other guard here waves it through, and the buyer drives over for a
    car that is not on the forecourt.

    **Makes only, deliberately.** Escape, Focus, Soul and Fit are model names
    and also ordinary English; matching them would reject honest sentences, and
    a false positive is not cosmetic -- the buyer gets the escalation line
    instead of an answer and it reads as a dead bot. A make is a proper noun
    that stays a proper noun, and you cannot get a buyer to the wrong forecourt
    without naming one.

    Grounding is wider here than for numbers, and on purpose. A price has to be
    re-read every turn because it can change; a car that was found earlier in
    this conversation was really found, and the buyer is still looking at it on
    their screen. So anything any tool returned in this thread counts, as does
    a make the buyer themselves typed -- "no, nothing from Audi right now" is
    the honest answer to a question about Audis, not a claim about one.
    """
    grounded = " ".join([
        json.dumps(list(tool_results), default=str),
        json.dumps(list(prior_results or []), default=str),
        json.dumps(list(tool_inputs or []), default=str),
        buyer_text or "",
    ]).lower()
    said = set(re.split(r"[^a-z0-9]+", text.lower()))

    violations = []
    for make in sorted(makes):
        if make in AMBIGUOUS_MAKES:
            continue
        # Whole word against the reply, substring against the grounding: a tool
        # result writes "Mercedes-Benz" where a reply says "Mercedes".
        if make in said and make not in grounded:
            violations.append(f"unsourced vehicle make {make}")
    return violations


def check_turn_length(text: str, channel: str) -> bool:
    """Voice caps assistant text -- one idea per turn, roughly 15 words."""
    return channel != "voice" or len(text.split()) <= VOICE_MAX_WORDS


def booking_nudge(assistant_turns: int, booked: bool) -> str:
    """A standing instruction, not a one-shot: re-attempt after every answer."""
    if booked or assistant_turns < 3:
        return ""
    return (
        "You have had three turns without attempting a booking. Offer two concrete "
        "appointment times from check_availability in your next message."
    )


def run_guards(
    text: str,
    tool_results: list[dict],
    *,
    channel: str = "chat",
    attempt: int = 1,
    assistant_turns: int = 0,
    booked: bool = False,
    tool_inputs: list[dict] | None = None,
    buyer_text: str = "",
    makes: set[str] | None = None,
    prior_results: list[dict] | None = None,
) -> GuardResult:
    violations = check_unsourced_facts(
        text, tool_results, tool_inputs=tool_inputs, buyer_text=buyer_text
    )
    if makes:
        violations += check_unsourced_vehicles(
            text, tool_results, makes=makes, tool_inputs=tool_inputs,
            buyer_text=buyer_text, prior_results=prior_results,
        )

    if not check_turn_length(text, channel):
        violations.append("too long for voice")

    if violations:
        if attempt == 1:
            # First violation: discard and retry with a corrective note.
            return GuardResult(ok=False, text=text, violations=violations)
        # Second violation: stop trying and get a person.
        return GuardResult(
            ok=False,
            text=safe_fallback(assistant_turns),
            violations=violations,
            should_escalate=True,
        )

    return GuardResult(
        ok=True, text=text, nudge=booking_nudge(assistant_turns, booked)
    )


def corrective_note(violations: list[str]) -> str:
    """The retry instruction. It has to say it is not the buyer talking.

    This goes back as a user-role turn, because that is the only role every
    vendor accepts mid-conversation. A model read it as the buyer objecting and
    opened its next reply with "You're right -- I shouldn't have mentioned a
    price before it was looked up", apologising to someone who had said no such
    thing and never saw the draft. That reads as a bot caught out, over a
    correction the buyer was never part of.
    """
    return (
        "[SYSTEM NOTE -- not from the buyer. They never saw your last draft and "
        "are still waiting for their first answer.] That draft was discarded: "
        + "; ".join(violations)
        + ". Every number you state must come from a tool result in this turn. "
        "Look it up or leave it out. Now write the reply again as if for the "
        "first time -- do not apologise, do not agree with anything, and do not "
        "refer to this note or to the draft in any way."
    )


def tool_results_from_messages(raw: str) -> list[dict]:
    try:
        calls = json.loads(raw or "[]")
    except ValueError:
        return []
    return [c.get("result", {}) for c in calls if isinstance(c, dict)]
