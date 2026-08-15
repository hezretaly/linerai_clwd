"""The scripted agent (LLM_MODE=stub).

Not a mock. It is a small state machine over ``conversations.stage`` that calls
the *real* tools with real arguments and builds every reply out of the tool
result, never out of a constant -- so the guards have something genuine to check
and the prices in the transcript are the seeded ones. Booking a car in stub mode
writes the same rows, fires the same events and moves the same dashboard as the
live agent would.

Its honest limitation: free text that matches no rail gets a redirect and stays
in the current stage. Stub mode is for building, testing and network-failure
fallback -- not for improvising with a prospect. The UI says so.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.agent import tools
from app.models import Conversation, Rail

# Anchored on word characters at both ends so sentence punctuation ("...my
# email is a@b.com.") does not end up inside the address.
EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
NAME_IN_TEXT = re.compile(
    r"(?:i'?m|my name is|this is|it'?s)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.IGNORECASE
)
MONEY_IN_TEXT = re.compile(r"\$\s?([\d,]+)")

ESCALATION_PATTERNS = [
    (r"out[- ]the[- ]door|otd price|total with tax|drive[- ]?off price", "out_the_door_price"),
    (r"credit score|bad credit|bankrupt|repo|repossess|get approved|financing trouble",
     "financing_trouble"),
    (r"speak to a manager|talk to a manager|get me a manager|your manager", "asks_for_manager"),
    (r"today|tomorrow|by (?:friday|monday|the weekend)|right away|urgent", "urgency"),
    (r"ready to (?:buy|sign)|put (?:money|a deposit) down|take it today", "ready_to_sign"),
]

STAGE_ORDER = [
    "opening", "browsing", "vehicle_focus", "objection",
    "qualifying", "slot_offered", "contact_capture", "booked",
]


def _money(value: int | None) -> str:
    return f"${value:,}" if value else "priced on request"


def _match_rail(db: Session, convo: Conversation, text: str) -> Rail | None:
    """Rails are the buyer side of each stage, so they double as the stub's script."""
    candidates = (
        db.query(Rail)
        .filter(Rail.enabled.is_(True))
        .filter((Rail.stage == convo.stage) | (Rail.kind == "knowledge") | (Rail.stage == ""))
        .all()
    )
    lowered = text.lower().strip()
    for rail in candidates:
        if rail.message_text.lower().strip() == lowered:
            return rail
    # Loose match so typing something close to a chip still lands.
    best, best_score = None, 0
    tokens = {w for w in re.findall(r"[a-z]+", lowered) if len(w) > 3}
    for rail in candidates:
        rail_tokens = {w for w in re.findall(r"[a-z]+", rail.message_text.lower()) if len(w) > 3}
        if not rail_tokens:
            continue
        score = len(tokens & rail_tokens) / len(rail_tokens)
        if score > best_score:
            best, best_score = rail, score
    return best if best_score >= 0.5 else None


def detect_escalation(text: str) -> str | None:
    for pattern, rule_key in ESCALATION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return rule_key
    return None


#: Phrases that mean the car they are naming is *theirs*, not one of ours. A
#: buyer saying "I'm trading in my 2015 Civic" is not asking to look at the
#: Civic on the lot, and re-focusing on it would be the most confusing possible
#: reading of a sentence they meant helpfully.
OWN_CAR = re.compile(
    r"\b(trade|trading|trade-in|my current|i drive|i have a|i own|currently in|"
    r"driving a|got a)\b",
    re.IGNORECASE,
)


ORDINALS = [
    (r"\bfirst\b|\bone\b(?!\s*more)|\b1st\b", 0),
    (r"\bsecond\b|\b2nd\b", 1),
    (r"\bthird\b|\b3rd\b|\blast\b", 2),
]


def _referenced_vin(db: Session, convo: Conversation, text: str) -> str | None:
    """Resolve "the first one" / "the Sienna" against what the buyer was shown.

    Without this, a follow-up re-runs a search on the follow-up's own wording
    and can surface a different car than the one being discussed.
    """
    from app.models import Vehicle

    shown: list[str] = []
    try:
        shown = json.loads(convo.last_results_json or "[]")
    except ValueError:
        shown = []

    if shown:
        for pattern, index in ORDINALS:
            if re.search(pattern, text, re.IGNORECASE) and index < len(shown):
                return shown[index]

        # Named by make or model: "tell me about the Sienna". Not when the
        # sentence is about a car they already own -- "I'm trading in my old
        # BMW X5" re-targeted the thread onto *our* X5, which is the same
        # confusion `_switched_vehicle` guards against and reachable through
        # this older path too.
        if not OWN_CAR.search(text):
            for vin in shown:
                vehicle = db.query(Vehicle).filter_by(vin=vin).one_or_none()
                if vehicle is None:
                    continue
                if re.search(
                    rf"\b{re.escape(vehicle.model)}\b", text, re.IGNORECASE
                ) or re.search(rf"\b{re.escape(vehicle.make)}\b", text, re.IGNORECASE):
                    return vin

    if convo.focus_vehicle_id:
        focus = db.query(Vehicle).filter_by(id=convo.focus_vehicle_id).one_or_none()
        if focus is not None:
            return focus.vin
    return shown[0] if shown else None



def _switched_vehicle(db: Session, convo: Conversation, text: str):
    """A car they have just named that is not the one we were discussing.

    A buyer changing their mind about which vehicle is the single most common
    thing that breaks a linear script, and this one is a state machine: once
    the conversation reached contact_capture, "actually, tell me about the X5"
    was read as an answer to "what is your email?" and the appointment was
    booked against the first car. The buyer's page then showed them coming in
    to see a vehicle they had explicitly moved off.

    Matched on the model name rather than the make -- "the Audi" is ambiguous
    on a lot with four of them, while "X5" is not -- and never when the
    sentence is about a car they already own.
    """
    from app.models import Vehicle

    if OWN_CAR.search(text):
        return None

    # Only a *change*. With no focus yet there is nothing to switch from, and
    # the opening turn is meant to search and offer a shortlist -- jumping
    # straight to the one car they named skips the three the lot actually has,
    # which is the whole first move of the conversation.
    focus_id = convo.focus_vehicle_id
    if not focus_id:
        return None
    for vehicle in db.query(Vehicle).filter_by(status="available").all():
        if vehicle.id == focus_id or not vehicle.rule_discuss:
            continue
        model = (vehicle.model or "").strip()
        if len(model) < 2:
            continue
        if re.search(rf"\b{re.escape(model)}\b", text, re.IGNORECASE):
            return vehicle
    return None


def _budget_from(text: str) -> int | None:
    match = MONEY_IN_TEXT.search(text)
    if match:
        return int(match.group(1).replace(",", ""))
    k = re.search(r"(\d{1,3})\s*k\b", text, re.IGNORECASE)
    return int(k.group(1)) * 1000 if k else None


def run_turn(db: Session, convo: Conversation, text: str) -> tuple[str, list[dict]]:
    """Return (assistant_text, tool_calls) for one buyer turn.

    tool_calls is a list of {name, input, result} so the guards can verify every
    number in the reply against something a tool actually returned.
    """
    calls: list[dict] = []

    def call(name: str, args: dict, tool_call_id: str | None = None) -> dict:
        result = tools.execute(db, convo, name, args, tool_call_id)
        calls.append({"name": name, "input": args, "result": result})
        return result

    rule_key = detect_escalation(text)
    if rule_key:
        try:
            result = call("escalate_to_human", {
                "rule_key": rule_key,
                "reason": f"Buyer said: {text[:160]}",
            }, tool_call_id=f"stub-esc-{convo.id}-{len(text)}")
            if result.get("escalated"):
                return (
                    "That's one for a person rather than me -- I've asked a rep to jump in "
                    "with the exact answer. They'll pick this up shortly.",
                    calls,
                )
        except tools.ToolError:
            pass

    rail = _match_rail(db, convo, text)
    knowledge_answer = None
    if rail is not None and rail.kind == "knowledge":
        entry = tools.lookup_knowledge(db, rail.message_text)
        if entry is not None:
            entry.use_count += 1
            db.commit()
            knowledge_answer = entry.answer
    elif rail is None:
        entry = tools.lookup_knowledge(db, text)
        if entry is not None and len(text.split()) > 2:
            knowledge_answer = entry.answer

    if knowledge_answer:
        return knowledge_answer + " Anything else you want to know before we set a time?", calls

    stage = convo.stage
    next_stage = rail.advances_to if rail and rail.advances_to else stage

    # An email address is an unambiguous "book me" at any stage. Without this,
    # rail matching can route a buyer who just handed over their details back
    # into another round of slot offers.
    if EMAIL_IN_TEXT.search(text) and stage != "booked":
        next_stage = "contact_capture"

    # Naming a different car is a change of subject, whatever stage the script
    # had reached. Checked after the email rule so somebody handing over their
    # details is still taken as booking, and before the stage branches so it
    # can override any of them.
    switched = _switched_vehicle(db, convo, text)
    if switched is not None:
        next_stage = "vehicle_focus"

    # ---- browsing: search real inventory --------------------------------
    if next_stage == "browsing" or stage in {"opening", "browsing"} and next_stage == stage:
        args: dict = {"keywords": text}
        budget = _budget_from(text)
        if budget:
            args["max_price"] = budget
        if re.search(r"third row|3rd row|seven seat|7 seat|kids|family", text, re.IGNORECASE):
            args["min_seats"] = 7
        result = call("search_inventory", args)
        vehicles = result["vehicles"]
        if not vehicles:
            convo.stage = "browsing"
            db.commit()
            return (
                "I don't have anything matching that on the lot right now. Want me to widen "
                "the search, or tell me what matters most and I'll work from there?",
                calls,
            )

        shown = vehicles[:3]
        # The buyer reads this list top to bottom, so "the first one" has to
        # mean the first row here -- record the order they actually saw.
        convo.last_results_json = json.dumps([v["vin"] for v in shown])
        lines = [
            f"{v['year']} {v['make']} {v['model']} {v['trim']}".strip()
            + f" -- {_money(v['price'])}, {v['mileage']:,} miles"
            for v in shown
        ]

        count = {2: "two", 3: "three"}.get(len(shown), str(len(shown)))
        cheapest = min(shown, key=lambda v: v["price"] or 10**9)
        fewest = min(shown, key=lambda v: v["mileage"] or 10**9)
        if len(shown) == 1:
            top, reason = shown[0], "the only one that fits right now"
        elif cheapest["vin"] == fewest["vin"]:
            top, reason = cheapest, f"the cheapest of the {count} and the lowest mileage"
        else:
            top, reason = fewest, f"the lowest mileage of the {count}"

        convo.stage = "browsing"
        convo.focus_vehicle_id = None
        db.commit()
        body = "Here's what fits:\n" + "\n".join(lines)
        return (
            f"{body}\n\nI'd start with the {top['make']} {top['model']} -- it's {reason}. "
            "Want the details on that one?",
            calls,
        )

    # ---- vehicle_focus: pull one real record ----------------------------
    if next_stage == "vehicle_focus":
        # An explicitly named car wins over "the one we were talking about" --
        # `_referenced_vin` falls back to the current focus, which is exactly
        # the car the buyer has just moved off.
        vin = switched.vin if switched is not None else _referenced_vin(db, convo, text)
        if vin is None:
            found = call("search_inventory", {"keywords": text})
            if not found["vehicles"]:
                return "Let me know which one you'd like to look at and I'll pull it up.", calls
            vin = found["vehicles"][0]["vin"]

        v = call("get_vehicle", {"vin": vin})
        # A buyer who has already booked and is now asking about a different
        # car is still booked -- the appointment is real, and walking the stage
        # back would make the row stop claiming a visit that exists. Only the
        # focus follows them, which `get_vehicle` has just moved.
        if convo.stage != "booked":
            convo.stage = "vehicle_focus"
        db.commit()
        parts = [
            f"The {v['year']} {v['make']} {v['model']} {v['trim']}".strip()
            + f" is {_money(v['price'])} with {v['mileage']:,} miles."
        ]
        if v.get("features"):
            parts.append(f"It has {', '.join(v['features'][:3])}.")
        if v.get("warranty_note"):
            parts.append(
                "It also comes with our 90-day, 4,000-mile limited powertrain warranty."
            )
        parts.append("Want to come see it?")
        return " ".join(parts), calls

    # ---- objection: answer from the vehicle's own rules ------------------
    if next_stage == "objection":
        from app.models import Vehicle

        focus = (
            db.query(Vehicle).filter_by(id=convo.focus_vehicle_id).one_or_none()
            if convo.focus_vehicle_id else None
        )
        convo.stage = "objection"
        db.commit()
        if focus is not None and focus.rule_hold_price:
            v = call("get_vehicle", {"vin": focus.vin})
            return (
                f"That one's priced firm at {_money(v['price'])} -- it's already at market. "
                "The best way to talk numbers is in person with a rep. Shall I get you a time?",
                calls,
            )
        return (
            "Pricing is something a rep handles in person, and they've got more room to work "
            "with once you're here. Want me to set up a time?",
            calls,
        )

    # ---- qualifying: record what the buyer actually said ------------------
    if next_stage == "qualifying":
        fields = []
        budget = _budget_from(text)
        if budget:
            fields.append({"key": "budget", "value": f"${budget:,}", "provenance": "inferred"})
        if re.search(r"financ", text, re.IGNORECASE):
            fields.append({"key": "financing", "value": text.strip()[:80],
                           "provenance": "typed"})
        if re.search(r"trade", text, re.IGNORECASE):
            fields.append({"key": "trade_in", "value": text.strip()[:80],
                           "provenance": "typed"})
        if re.search(r"week|month|soon|days", text, re.IGNORECASE):
            fields.append({"key": "timeline", "value": text.strip()[:80],
                           "provenance": "typed"})
        if fields and convo.lead_id:
            call("save_captured_fields", {"fields": fields})
        convo.stage = "qualifying"
        db.commit()
        return (
            "Got it, that helps. The quickest way forward is to get you in front of the car "
            "-- want me to find a time?",
            calls,
        )

    # ---- slot_offered: two concrete times, never "when works for you?" ----
    if next_stage == "slot_offered":
        period = "any"
        if re.search(r"morning", text, re.IGNORECASE):
            period = "morning"
        elif re.search(r"afternoon|later", text, re.IGNORECASE):
            period = "afternoon"
        elif re.search(r"evening|after work", text, re.IGNORECASE):
            period = "evening"

        lowered = text.lower()
        asked_day = next((d for d in WEEKDAYS if d in lowered), "")

        result = call("check_availability", {
            "days_ahead": 10 if asked_day else 7, "preferred_period": period,
        })
        slots = result["slots"]
        if asked_day:
            # A named day is a request, not a hint -- offering a different one
            # and calling it an answer is how a buyer ends up booked wrong.
            on_day = [
                s for s in slots
                if datetime.fromisoformat(s).strftime("%A").lower() == asked_day
            ]
            if on_day:
                slots = on_day
            else:
                return (
                    f"I don't have anything open on {asked_day.title()}. "
                    "What other day works for you?",
                    calls,
                )

        if not slots:
            return "We're fully booked this week. Want me to look at next week?", calls
        first, second = _spread(slots)
        # Remember exactly what was offered, so the booking uses the slot the
        # buyer picks rather than whatever comes back first next time.
        convo.offered_slots_json = json.dumps([first, second])
        convo.chosen_slot = ""
        convo.stage = "slot_offered"
        db.commit()
        # The booking card below this message already shows every open day
        # and time, plus the boxes for their details. Listing two of them here
        # as well is the same question asked twice, and the buyer answers the
        # one that is harder to act on.
        return ("Here's what's open this week -- pick whichever suits you.", calls)

    # ---- contact_capture -> book -----------------------------------------
    if next_stage in {"contact_capture", "booked"}:
        email_match = EMAIL_IN_TEXT.search(text)
        name_match = NAME_IN_TEXT.search(text)

        # Resolve which of the offered times the buyer just agreed to.
        chosen, unmet_day, unmet_period = "", "", ""
        if convo.chosen_slot:
            chosen = convo.chosen_slot
        else:
            chosen, unmet_day, unmet_period = _pick_offered_slot(convo, text)

        if unmet_day or unmet_period:
            # They asked for a day or time we did not offer. Go and look rather
            # than booking them into the slot they did not ask for.
            result = call("check_availability", {
                "days_ahead": 10, "preferred_period": unmet_period or "any",
            })
            slots = result["slots"]
            if unmet_day:
                slots = [
                    s for s in slots
                    if datetime.fromisoformat(s).strftime("%A").lower() == unmet_day
                ]

            asked = " ".join(w for w in (unmet_day.title(), unmet_period) if w)
            if not slots:
                convo.stage = "slot_offered"
                db.commit()
                return (
                    f"I don't have anything open {asked}. Would one of the times I "
                    "mentioned work, or should I look further out?",
                    calls,
                )

            first, second = _spread(slots)
            convo.offered_slots_json = json.dumps([first, second])
            convo.stage = "slot_offered"
            db.commit()
            return (f"Here's what I have {asked} -- take your pick.", calls)

        if chosen:
            convo.chosen_slot = chosen

        if not email_match:
            convo.stage = "contact_capture"
            db.commit()
            when = f" for {_slot_label(chosen)}" if chosen else ""
            return (
                f"Perfect{when}. Can I get your name and the best email for you? I'll send "
                "the confirmation and the address there.",
                calls,
            )

        if not chosen:
            # Fall back to the first slot we actually offered, not to whatever a
            # fresh lookup returns -- booking a buyer into a time that was never
            # put in front of them is the failure this whole path guards against.
            try:
                offered = json.loads(convo.offered_slots_json or "[]")
            except ValueError:
                offered = []
            if offered:
                chosen = offered[0]
            else:
                availability = call("check_availability", {"days_ahead": 7})
                if not availability["slots"]:
                    return "That time just went. Want me to look at next week?", calls
                chosen = availability["slots"][0]

        try:
            result = call("book_appointment", {
                "name": name_match.group(1) if name_match else email_match.group(0).split("@")[0],
                "email": email_match.group(0),
                "starts_at": chosen,
            }, tool_call_id=f"stub-book-{convo.id}")
        except tools.ToolError as exc:
            return f"I couldn't lock that in -- {exc}", calls

        vehicle = result.get("vehicle")
        when = _slot_label(result["starts_at"])
        car = (
            f" to see the {vehicle['year']} {vehicle['make']} {vehicle['model']}"
            if vehicle else ""
        )
        return (
            f"Booked -- {when}{car}. I've sent a confirmation to "
            f"{email_match.group(0)}. See you then!",
            calls,
        )

    # ---- anything unmatched ---------------------------------------------
    # The honest limitation: the stub does not improvise. It redirects and
    # holds the stage rather than inventing an answer.
    return (
        "I can help you find the right vehicle and get you booked in for a look. "
        "What are you after -- a budget, a body style, or something specific?",
        calls,
    )


PERIODS = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _pick_offered_slot(convo: Conversation, text: str) -> tuple[str, str, str]:
    """Match "Saturday morning works" against the two times actually offered.

    Returns (slot, unmet_day, unmet_period). A buyer naming a day or a time of
    day we did not offer must never be quietly booked into a different one --
    saying "Saturday morning" to an offer of Friday 8 AM is not agreement to
    Friday. When the request cannot be met from the offered pair the slot comes
    back empty and the caller goes and looks for what was actually asked for.
    """
    try:
        offered: list[str] = json.loads(convo.offered_slots_json or "[]")
    except ValueError:
        return "", "", ""
    if not offered:
        return "", "", ""

    lowered = text.lower()
    candidates = [(slot, datetime.fromisoformat(slot)) for slot in offered]
    asked_day = next((d for d in WEEKDAYS if d in lowered), "")
    asked_period = next((p for p in PERIODS if p in lowered), "")

    pool = [s for s, _ in candidates]
    if asked_day:
        on_day = [s for s, when in candidates if when.strftime("%A").lower() == asked_day]
        if not on_day:
            # They named a day we never offered. Go and look for it.
            return "", asked_day, asked_period
        pool = on_day

    if asked_period:
        lo, hi = PERIODS[asked_period]
        in_period = [s for s in pool if lo <= datetime.fromisoformat(s).hour < hi]
        if not in_period:
            return "", asked_day, asked_period
        pool = in_period

    if asked_day or asked_period:
        return pool[0], "", ""

    # "the first one" / "the second" / "either".
    if re.search(r"\bfirst\b|\bearlier\b", lowered):
        return pool[0], "", ""
    if re.search(r"\bsecond\b|\blater\b", lowered):
        return pool[-1], "", ""
    return (pool[0], "", "") if len(pool) == 1 else ("", "", "")


def _spread(slots: list[str]) -> tuple[str, str]:
    """Two genuinely different options.

    Offering 8:00 and 8:30 is technically two choices and practically one --
    pick a second slot on another day, or failing that several hours later.
    """
    first = slots[0]
    head = datetime.fromisoformat(first)
    # Best: another day at another time of day, so "Saturday morning" and
    # "Friday afternoon" are distinguishable by either half of the phrase.
    for candidate in slots[1:]:
        when = datetime.fromisoformat(candidate)
        if when.date() != head.date() and when.hour != head.hour:
            return first, candidate
    for candidate in slots[1:]:
        if datetime.fromisoformat(candidate).date() != head.date():
            return first, candidate
    for candidate in slots[1:]:
        if abs((datetime.fromisoformat(candidate) - head).total_seconds()) >= 3 * 3600:
            return first, candidate
    return first, slots[1] if len(slots) > 1 else first


def _slot_label(iso: str) -> str:
    when = datetime.fromisoformat(iso)
    hour = when.hour % 12 or 12
    ampm = "AM" if when.hour < 12 else "PM"
    minute = f":{when.minute:02d}" if when.minute else ":00"
    return f"{when.strftime('%A')} at {hour}{minute} {ampm}"


def next_stage_for(convo: Conversation) -> str:
    return convo.stage
