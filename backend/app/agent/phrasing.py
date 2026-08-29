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


def money(value: int | None) -> str:
    return f"${value:,}" if value else "price on request"


def one_line(vehicle: dict) -> str:
    """`2020 Honda Accord Sport -- $21,400, 38,120 miles`."""
    title = f"{vehicle['year']} {vehicle['make']} {vehicle['model']} {vehicle.get('trim') or ''}"
    line = f"{title.strip()} -- {money(vehicle.get('price'))}"
    mileage = vehicle.get("mileage")
    if mileage:
        line += f", {mileage:,} miles"
    return line


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
