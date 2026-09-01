"""Chips that answer themselves, without a model turn.

**Why.** A rail is a button the dealership put on screen, and its meaning is
fixed: "What's under $20k?" means `search_inventory(max_price=20000)` and
nothing else. Sending that through a model asks it to re-derive an intent
somebody already decided, and pays for the privilege -- a round trip, a couple
of seconds in front of a buyer, and one more place a number could come out
wrong. The tool is the same tool either way; only the deciding is skipped.

**Free text still goes to the model.** This is deliberately not a return to the
scripted assistant: a buyer who *types* "anything cheap and reliable" gets the
model reading the sentence, which is the thing being sold. What is short-
circuited is the case where the question was pre-written by the dealership.

**The sentence and the search cannot disagree**, because the sentence is built
from the arguments the search actually ran with. A lead-in reading "under
$20,000" over a `max_price` of 25,000 is the failure this shape prevents;
writing both by hand is how it happens.

**Two of them are relative to what the buyer is looking at.** "Anything
cheaper?" has no fixed price -- it means cheaper than the cars on screen -- so
it reads `conversations.last_results_json`, the same list "the first one"
resolves against. With nothing shown yet there is nothing to be cheaper than,
and the chip is not offered (see `runner.rails_for`).

**Guards still run.** A templated reply is sourced by construction, so it
passes; running them anyway is what would catch a template that stopped being.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agent import details, phrasing, tools
from app.models import Conversation, Rail, Vehicle

#: How many cars a chip answers with. Three is what the stub shows and what
#: fits on a phone without scrolling.
SHOWN = 3


def _money_words(value: int) -> str:
    return f"${value:,}"


def search(db: Session, convo: Conversation, args: dict, lead_in: str) -> tuple[str, list[dict]]:
    result = tools.search_inventory(db, convo, args)
    calls = [{"name": "search_inventory", "input": args, "result": result}]
    vehicles = result.get("vehicles") or []
    if not vehicles:
        return phrasing.nothing_found(), calls

    shown = vehicles[:SHOWN]
    # The buyer reads this list top to bottom, so "the first one" has to mean
    # the first row here -- record the order they actually saw.
    convo.last_results_json = json.dumps([v["vin"] for v in shown])
    convo.stage = "browsing"
    convo.focus_vehicle_id = None
    db.commit()
    return phrasing.describe_results(shown, lead_in=lead_in), calls


def _shown_vehicles(db: Session, convo: Conversation) -> list[Vehicle]:
    vins = json.loads(convo.last_results_json or "[]")
    if not vins:
        return []
    return db.query(Vehicle).filter(Vehicle.vin.in_(vins)).all()


# --------------------------------------------------------------------------
# The actions themselves. Each takes (db, convo, args) and returns the same
# (reply, calls) pair a model turn does, so the caller cannot tell them apart.
# --------------------------------------------------------------------------

def under_price(db: Session, convo: Conversation, args: dict) -> tuple[str, list[dict]]:
    cap = int(args.get("max_price") or 0)
    return search(db, convo, {"max_price": cap},
                  f"Here's what we have under {_money_words(cap)}:")


def with_seats(db: Session, convo: Conversation, args: dict) -> tuple[str, list[dict]]:
    seats = int(args.get("min_seats") or 7)
    return search(db, convo, {"min_seats": seats},
                  f"Here's what seats {seats} or more:")


def matching(db: Session, convo: Conversation, args: dict) -> tuple[str, list[dict]]:
    """A keyword search whose lead-in the dealership wrote.

    `lead_in` is on the action rather than derived, because "for a daily
    commute" is a phrase about the buyer's need and cannot be read back out of
    `keywords="commuter reliable"` without inventing English.
    """
    words = str(args.get("keywords") or "").strip()
    return search(db, convo, {"keywords": words},
                  str(args.get("lead_in") or "Here's what fits:"))


def cheaper(db: Session, convo: Conversation, args: dict) -> tuple[str, list[dict]]:
    """Cheaper than the cheapest thing currently on screen.

    Relative, so the bound is computed from what was shown rather than written
    into the chip -- a fixed price would answer "anything cheaper?" with the
    same three cars as soon as the buyer had narrowed past it once.

    **The bound is not spoken, and that is the guard's rule rather than a
    style choice.** A price is sourced only by *this* turn's tool result, its
    input, or the buyer's own words -- a figure from an earlier turn is
    explicitly not, because a price is re-read every turn precisely because it
    can change. So the lead-in says "cheaper" and the numbers the buyer reads
    are the ones the search just returned. Written with the number in, the
    guard rejected the whole reply and the buyer got the escalation line
    instead of three cars. Measured, not reasoned.
    """
    prices = [v.price for v in _shown_vehicles(db, convo) if v.price]
    if not prices:
        return phrasing.nothing_found("cheaper to compare against"), []
    reply, calls = search(db, convo, {"max_price": min(prices) - 1},
                          "Here's what comes in cheaper:")
    if not (calls and calls[0]["result"].get("vehicles")):
        return ("Those are the cheapest I have in that shape right now. Widen it a little "
                "and I'll look again, or tell me what matters most."), calls
    return reply, calls


def fewer_miles(db: Session, convo: Conversation, args: dict) -> tuple[str, list[dict]]:
    """Lower mileage than the lowest currently on screen.

    Same shape as `cheaper`, and the mileage bound is left out of the sentence
    for the same reason: it is a number from a previous turn.
    """
    miles = [v.mileage for v in _shown_vehicles(db, convo) if v.mileage]
    if not miles:
        return phrasing.nothing_found("to compare mileage against"), []
    reply, calls = search(db, convo, {"max_mileage": min(miles) - 1},
                          "Here's what has fewer miles:")
    if not (calls and calls[0]["result"].get("vehicles")):
        return ("Those are the lowest mileage I have right now. Happy to look at something "
                "else if that is the thing that matters."), calls
    return reply, calls


def call_me(db: Session, convo: Conversation, args: dict) -> tuple[str, list[dict]]:
    """"Have someone call me" -- put the details card up. No model turn.

    The most fixed meaning of any chip here: a buyer pressing it has decided
    they want a person, and there is nothing for a model to interpret. It is
    also the one ask a chip could never make on its own -- a chip's text is
    sent as the buyer's own message, so a pre-written "my number is..." would
    put words in their mouth. The card asks; the buyer types; the answer is
    theirs.

    The lead-in and the boxes come from the same call, so they cannot disagree
    about what is being asked for -- the rule every chip in this file follows.
    """
    result = tools.request_details(db, convo, {
        "fields": args.get("fields") or list(details.DEFAULT_KEYS),
        "reason": args.get("reason") or "",
    })
    calls = [{"name": "request_details", "input": args, "result": result}]
    return (
        "Of course. Pop your details in below and someone here will give you a "
        "ring.",
        calls,
    )


#: The whole set. A rail's `action_json` names one of these keys and carries
#: its arguments; anything else -- including a key that used to exist and no
#: longer does -- falls through to the model, which is the behaviour every
#: chip had before this file.
ACTIONS = {
    "under_price": under_price,
    "with_seats": with_seats,
    "matching": matching,
    "cheaper": cheaper,
    "fewer_miles": fewer_miles,
    "call_me": call_me,
}

#: Actions that answer relative to the cars already on screen. `rails_for`
#: drops these chips when nothing has been shown yet: "anything cheaper?"
#: before any search is a question about nothing.
NEEDS_RESULTS = {"cheaper", "fewer_miles"}


def action_of(rail: Rail | None) -> tuple[str, dict]:
    """The action a rail carries, or `("", {})`. Never raises.

    A malformed `action_json` is a seed or an edit that went wrong, and the
    right answer is the model turn the chip would have had anyway -- not a 500
    in front of a buyer.
    """
    if rail is None or not getattr(rail, "action_json", ""):
        return "", {}
    try:
        payload = json.loads(rail.action_json)
    except (TypeError, ValueError):
        return "", {}
    name = str(payload.get("do") or "")
    if name not in ACTIONS:
        return "", {}
    return name, payload.get("args") or {}


def run(db: Session, convo: Conversation, rail: Rail) -> tuple[str, list[dict]] | None:
    """Answer the chip deterministically, or None to let the model have it."""
    name, args = action_of(rail)
    if not name:
        return None
    return ACTIONS[name](db, convo, args)
