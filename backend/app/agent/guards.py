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

SAFE_FALLBACK = (
    "I want to get that exactly right rather than guess -- let me bring in one of our "
    "people to confirm the details for you."
)

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


def check_unsourced_facts(text: str, tool_results: list[dict]) -> list[str]:
    grounded = sourced_numbers(tool_results)
    violations: list[str] = []

    for raw in MONEY_RE.findall(text):
        value = raw.replace(",", "").split(".")[0]
        if value not in grounded:
            violations.append(f"unsourced price ${raw}")

    for raw in MILEAGE_RE.findall(text):
        value = raw.replace(",", "")
        if value not in grounded:
            violations.append(f"unsourced mileage {raw}")

    for raw in YEAR_RE.findall(text):
        if raw not in grounded:
            violations.append(f"unsourced model year {raw}")

    if AVAILABILITY_RE.search(text) and not tool_results:
        violations.append("availability claim with no inventory lookup this turn")

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
) -> GuardResult:
    violations = check_unsourced_facts(text, tool_results)

    if not check_turn_length(text, channel):
        violations.append("too long for voice")

    if violations:
        if attempt == 1:
            # First violation: discard and retry with a corrective note.
            return GuardResult(ok=False, text=text, violations=violations)
        # Second violation: stop trying and get a person.
        return GuardResult(
            ok=False, text=SAFE_FALLBACK, violations=violations, should_escalate=True
        )

    return GuardResult(
        ok=True, text=text, nudge=booking_nudge(assistant_turns, booked)
    )


def corrective_note(violations: list[str]) -> str:
    return (
        "Your previous draft was rejected: "
        + "; ".join(violations)
        + ". Every number you state must come from a tool result in this turn. "
        "Look it up or leave it out."
    )


def tool_results_from_messages(raw: str) -> list[dict]:
    try:
        calls = json.loads(raw or "[]")
    except ValueError:
        return []
    return [c.get("result", {}) for c in calls if isinstance(c, dict)]
