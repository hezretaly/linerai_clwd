"""Riverside Auto fixture.

Reproduces the dataset the mockups describe (§18.1): a fully populated
"yesterday" so no dashboard screen is ever empty on first run. Everything here
is a real row read by the real agent -- narrow, not fake.

Idempotent by wipe: ``seed()`` clears the tables it owns and rebuilds them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import yaml
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, create_all, utcnow
from app.models import (
    Appointment,
    AssistantSettings,
    CapturedField,
    Conversation,
    Dealership,
    Escalation,
    Event,
    HandoffRule,
    IngestRun,
    KnowledgeEntry,
    Lead,
    Message,
    Outreach,
    Rail,
    User,
    Vehicle,
    VehicleMention,
)

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# One password per role, both from settings. On a laptop these are the
# documented dev value; in production config.py refuses to boot until each has
# been set to something real and the two differ.
SEED_PASSWORD = settings.manager_password  # kept for scripts that import it


def _password_for(role: str) -> str:
    return settings.manager_password if role == "manager" else settings.rep_password


def _hash(password: str) -> str:
    return pwd.hash(password)


# --------------------------------------------------------------------------
# Vehicles
# --------------------------------------------------------------------------
# (vin, year, make, model, trim, price, mileage, body_style, seats, keywords, features)
# `keywords` is what search matches on; `features` is what Liner is allowed to
# read back to a buyer, so it has to be phrased the way a person would say it.
VEHICLES = [
    ("1HGCV1F34LA015782", 2020, "Honda", "Accord", "Sport", 21400, 38120, "Sedan", 5,
     "commuter fuel efficient apple carplay one owner",
     ["Apple CarPlay", "adaptive cruise control", "one owner", "heated seats"]),
    ("5TDKZ3DC8JS905311", 2018, "Toyota", "Sienna", "LE", 19850, 74300, "Minivan", 8,
     "third row family sliding doors seven seats minivan",
     ["eight seats", "power sliding doors", "rear climate control", "backup camera"]),
    ("2C4RC1BG7KR522104", 2019, "Chrysler", "Pacifica", "Touring L", 18900, 81500, "Minivan", 7,
     "third row family stow n go minivan",
     ["Stow 'n Go seating", "power liftgate", "tri-zone climate", "backup camera"]),
    ("1FTEW1EP7JKD41209", 2018, "Ford", "F-150", "XLT", 27600, 68240, "Truck", 5,
     "towing crew cab four wheel drive truck",
     ["crew cab", "four-wheel drive", "tow package", "bed liner"]),
    ("3VW217AU9HM045118", 2017, "Volkswagen", "Golf", "S", 13950, 92400, "Hatchback", 5,
     "budget commuter manual hatch",
     ["heated seats", "Bluetooth", "alloy wheels"]),
    ("KM8J3CA46JU622180", 2018, "Hyundai", "Tucson", "SEL", 16750, 79880, "SUV", 5,
     "budget suv all wheel drive heated seats",
     ["all-wheel drive", "heated seats", "blind spot monitoring", "Apple CarPlay"]),
    ("1N4AL3AP7JC201955", 2018, "Nissan", "Altima", "SV", 14200, 88600, "Sedan", 5,
     "budget commuter backup camera",
     ["backup camera", "Bluetooth", "keyless entry"]),
    ("5XYPH4A54KG455012", 2019, "Kia", "Sorento", "EX", 22900, 61200, "SUV", 7,
     "third row awd leather suv",
     ["third-row seating", "all-wheel drive", "leather", "power liftgate"]),
    ("WBA8E9G59JNU22771", 2018, "BMW", "330i", "xDrive", 23400, 54900, "Sedan", 5,
     "luxury awd sport",
     ["all-wheel drive", "leather", "sunroof", "navigation"]),
    ("1G1ZD5ST4LF071244", 2020, "Chevrolet", "Malibu", "LT", 17300, 47110, "Sedan", 5,
     "budget commuter low miles",
     ["Apple CarPlay", "backup camera", "remote start"]),
    ("JTMRFREV8HJ135806", 2017, "Toyota", "RAV4", "XLE", 18450, 86750, "SUV", 5,
     "reliable awd sunroof suv",
     ["all-wheel drive", "sunroof", "backup camera", "dual-zone climate"]),
    ("1C4RJFAG9JC301877", 2018, "Jeep", "Grand Cherokee", "Laredo", 21950, 71300, "SUV", 5,
     "four wheel drive towing suv",
     ["four-wheel drive", "tow package", "heated seats"]),
    ("3GNKBBRA1KS587440", 2019, "Chevrolet", "Blazer", "LT", 24100, 52880, "SUV", 5,
     "awd apple carplay suv",
     ["all-wheel drive", "Apple CarPlay", "power liftgate", "heated seats"]),
    ("1HGCR2F31HA108422", 2017, "Honda", "Civic", "LX", 12900, 98400, "Sedan", 5,
     "budget commuter fuel efficient first car",
     ["backup camera", "Bluetooth", "40 mpg highway"]),
]

KNOWLEDGE = [
    ("Doc fee", "Our documentation fee is $189, and it's the same on every vehicle. It's "
     "listed on the buyer's order before anything is signed."),
    ("Trade-ins", "Yes, we take trade-ins. Bring the vehicle, the title and any payoff "
     "information and we'll appraise it while you're here -- usually about 20 minutes."),
    ("Deposits", "A $500 refundable deposit holds a vehicle for up to 48 hours. It comes "
     "straight off the purchase price, or back to your card if you pass."),
    ("Financing", "We work with eleven lenders including two credit unions. A rep handles "
     "the application in person -- Liner can't quote rates or approvals."),
    ("Warranty", "Every vehicle under 80,000 miles includes a 90-day/4,000-mile limited "
     "powertrain warranty at no cost. Extended coverage is available."),
    ("Out-of-state buyers", "We sell out of state regularly. We collect your state's sales "
     "tax and handle the title paperwork; plates go through your own DMV."),
    ("Hours and location", "We're at 4820 Riverside Parkway, Cedar Falls. Open Monday "
     "through Saturday, 8 AM to 8 PM. Closed Sunday."),
]

HANDOFF_RULES = [
    ("out_the_door_price", "Buyer asks for an out-the-door price",
     "A total number with tax, title and fees is a negotiation, not a question.",
     None, "", "sales_manager"),
    ("financing_trouble", "Financing or credit trouble comes up",
     "Anything about credit scores, approvals, bankruptcy or repossession.",
     None, "", "finance_manager"),
    ("asks_for_manager", "Buyer asks for a manager",
     "Taken literally, every time, with no attempt to resolve it first.",
     None, "", "sales_manager"),
    ("urgency", "Buyer signals urgency",
     "Needs a vehicle within a set number of days.",
     3, "days", "any_available"),
    ("ready_to_sign", "Buyer is ready to sign",
     "Explicit intent to purchase today or put money down.",
     None, "", "any_available"),
]

# Rails: (kind, stage, label, message_text, advances_to, sort, requires_vehicle)
RAILS = [
    ("opener", "opening", "What's under $20k?", "What do you have under $20,000?", "browsing", 1, False),
    ("opener", "opening", "Anything with a third row?",
     "I need something with a third row for the kids.", "browsing", 2, False),
    ("opener", "opening", "Something reliable for commuting",
     "I'm after something reliable for a daily commute.", "browsing", 3, False),

    ("followup", "browsing", "Tell me about the first one",
     "Tell me more about the first one.", "vehicle_focus", 1, False),
    ("followup", "browsing", "Anything cheaper?",
     "Do you have anything cheaper than those?", "browsing", 2, False),
    ("followup", "browsing", "Lower mileage options?",
     "Do you have anything with lower mileage?", "browsing", 3, False),

    ("followup", "vehicle_focus", "Can I see it this week?",
     "Can I come see it this week?", "slot_offered", 1, True),
    ("followup", "vehicle_focus", "Is the price negotiable?",
     "Is the price negotiable on that one?", "objection", 2, True),
    ("followup", "vehicle_focus", "How many miles on it?",
     "How many miles does it have?", "vehicle_focus", 3, True),

    ("followup", "objection", "That works for me",
     "Okay, that works for me.", "qualifying", 1, False),
    ("followup", "objection", "I'd have a trade-in",
     "I'd want to trade in my current car.", "qualifying", 2, False),

    ("followup", "qualifying", "Looking to buy in a couple weeks",
     "I'm hoping to buy in the next couple of weeks.", "slot_offered", 1, False),
    ("followup", "qualifying", "I'd be financing",
     "I'd be financing rather than paying cash.", "qualifying", 2, False),

    ("followup", "slot_offered", "Saturday morning works",
     "Saturday morning works for me.", "contact_capture", 1, False),
    ("followup", "slot_offered", "Anything later in the day?",
     "Do you have anything later in the day?", "slot_offered", 2, False),

    ("followup", "contact_capture", "Send it to my email",
     "I'm Jordan Reyes, and my email is jordan.reyes@example.com.", "booked", 1, False),

    ("followup", "booked", "What should I bring?",
     "What should I bring with me?", "booked", 1, False),

    ("knowledge", "", "What's your doc fee?", "What's your doc fee?", "", 1, False),
    ("knowledge", "", "Do you take trade-ins?", "Do you take trade-ins?", "", 2, False),
]


def _clear(db: Session) -> None:
    for model in (
        VehicleMention, Outreach, Escalation, Appointment, CapturedField, Message,
        Conversation, Lead, IngestRun, Vehicle, Rail, KnowledgeEntry, HandoffRule,
        AssistantSettings, User, Dealership, Event,
    ):
        db.query(model).delete()
    db.commit()


def _seed_dealership(db: Session) -> Dealership:
    raw = yaml.safe_load(settings.dealership_config.read_text())
    dealership = Dealership(
        name=raw["name"],
        timezone=raw.get("timezone", "America/Chicago"),
        hours_json=json.dumps(raw.get("hours", {})),
        address=raw.get("address", ""),
        phone=raw.get("phone", ""),
        website_url=raw.get("website_url") or "",
    )
    db.add(dealership)
    db.commit()
    return dealership


def _seed_users(db: Session) -> list[User]:
    people = [
        ("Dana Mercer", "dana.mercer@example.invalid", "manager", "DM", 6),
        ("Marcus Vale", "marcus.vale@example.invalid", "rep", "MV", 8),
        ("Priya Raman", "priya.raman@example.invalid", "rep", "PR", 8),
        ("Trevor Osei", "trevor.osei@example.invalid", "rep", "TO", 8),
    ]
    users = [
        User(
            name=name, email=email, password_hash=_hash(_password_for(role)),
            role=role, avatar_initials=initials, daily_cap=cap,
            notify_channel="email" if role == "manager" else "dashboard",
        )
        for name, email, role, initials, cap in people
    ]
    db.add_all(users)
    db.commit()
    return users


def _seed_vehicles(db: Session) -> list[Vehicle]:
    now = utcnow()
    vehicles = []
    for vin, year, make, model, trim, price, mileage, body, seats, keywords, features in VEHICLES:
        vehicles.append(
            Vehicle(
                vin=vin, year=year, make=make, model=model, trim=trim, price=price,
                mileage=mileage, body_style=body, seats=seats, keywords=keywords,
                features_json=json.dumps(features),
                photo_url=f"/api/photos/{vin}.svg",
                listing_url=f"/inventory/{vin}",
                status="available", source="seed",
                first_seen_at=now - timedelta(days=30), last_seen_at=now,
            )
        )
    # One sold car still being quoted -- this is what the blast-radius panel is for.
    vehicles[2].status = "sold"
    # One car the dealership does not want discussed. Filtered at the tool layer.
    vehicles[8].rule_discuss = False
    vehicles[8].rule_note = "Consignment -- owner has not signed the agreement yet."
    # A price the manager will not move on.
    vehicles[3].rule_hold_price = True
    vehicles[3].rule_note = "Priced to market. No discount without Dana's approval."
    vehicles[0].rule_mention_warranty = True

    db.add_all(vehicles)
    db.commit()
    return vehicles


def _seed_settings(db: Session, manager: User) -> None:
    live = AssistantSettings(
        version=7, status="live", tone="warm", push_level="balanced",
        price_mode="listed_only", financing_mode="refer_to_rep",
        after_hours_mode="full_service", booking_slot_length=30,
        greeting="Hi! I'm Liner, Riverside Auto's assistant. What are you looking for?",
        published_by=manager.id, published_at=utcnow() - timedelta(days=9),
    )
    draft = AssistantSettings(
        version=8, status="draft", tone="warm", push_level="assertive",
        price_mode="listed_only", financing_mode="refer_to_rep",
        after_hours_mode="full_service", booking_slot_length=30,
        greeting="Hi! I'm Liner, Riverside Auto's assistant. What are you looking for?",
    )
    db.add_all([live, draft])
    db.commit()


def _seed_rules_and_knowledge(db: Session) -> None:
    fired = {"out_the_door_price": 12, "financing_trouble": 5, "asks_for_manager": 3,
             "urgency": 8, "ready_to_sign": 4}
    for key, label, description, threshold, unit, route in HANDOFF_RULES:
        db.add(HandoffRule(
            key=key, label=label, description=description, enabled=True,
            threshold_value=threshold, threshold_unit=unit, route_target=route,
            notify="email_dashboard", fired_count=fired.get(key, 0),
        ))
    for topic, answer in KNOWLEDGE:
        db.add(KnowledgeEntry(topic=topic, answer=answer, use_count=0))
    db.commit()


def _seed_rails(db: Session) -> None:
    knowledge = {k.topic: k for k in db.query(KnowledgeEntry).all()}
    topic_for = {"What's your doc fee?": "Doc fee", "Do you take trade-ins?": "Trade-ins"}
    for kind, stage, label, text, advances, order, needs_vehicle in RAILS:
        entry = knowledge.get(topic_for.get(label, ""))
        db.add(Rail(
            kind=kind, stage=stage, label=label, message_text=text,
            advances_to=advances, sort_order=order, requires_vehicle=needs_vehicle,
            knowledge_entry_id=entry.id if entry else None, enabled=True,
        ))
    db.commit()


def _next_open_slot(hours: dict, now: datetime, days_ahead: int, hour: int) -> datetime:
    """Pick a real slot inside the dealership's hours.

    Timestamps are naive and interpreted in the dealership's local frame
    everywhere -- check_availability builds slots from hours_json the same way.
    Hardcoding an hour in the seed is how the fixture ends up with a 9 PM
    appointment the calendar cannot draw and book_appointment would reject.
    """
    day = (now + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    for _ in range(7):
        window = hours.get(DAY_NAMES[day.weekday()])
        if window and int(window["open"][:2]) <= hour < int(window["close"][:2]):
            return day
        day += timedelta(days=1)
    return day


def _seed_history(db: Session, users: list[User], vehicles: list[Vehicle]) -> None:
    """A populated yesterday: conversations, leads, appointments, one open escalation."""
    now = utcnow()
    hours = json.loads(db.query(Dealership).first().hours_json)
    manager, marcus, priya, trevor = users
    by_vin = {v.vin: v for v in vehicles}
    sienna = by_vin["5TDKZ3DC8JS905311"]
    pacifica = by_vin["2C4RC1BG7KR522104"]  # the sold one
    f150 = by_vin["1FTEW1EP7JKD41209"]
    civic = by_vin["1HGCR2F31HA108422"]
    accord = by_vin["1HGCV1F34LA015782"]

    def add_lead(name, email, phone, source, assigned, hours_ago) -> Lead:
        lead = Lead(
            name=name, email=email, phone=phone, source=source,
            assigned_user_id=assigned.id if assigned else None,
            email_consent_at=now - timedelta(hours=hours_ago) if email else None,
            created_at=now - timedelta(hours=hours_ago),
        )
        db.add(lead)
        db.flush()
        return lead

    def add_convo(lead, channel, stage, turns, hours_ago, *, status="closed",
                  paused=False, focus=None) -> Conversation:
        convo = Conversation(
            lead_id=lead.id if lead else None, channel=channel, status=status,
            agent_paused=paused, stage=stage,
            focus_vehicle_id=focus.id if focus else None,
            started_at=now - timedelta(hours=hours_ago),
            ended_at=None if status == "active" else now - timedelta(hours=hours_ago - 0.4),
            summary=turns[-1][1][:140] if turns else "",
        )
        db.add(convo)
        db.flush()
        offset = 0
        for role, content in turns:
            db.add(Message(
                conversation_id=convo.id, role=role, content=content,
                created_at=now - timedelta(hours=hours_ago) + timedelta(seconds=offset),
            ))
            offset += 45
        db.flush()
        return convo

    def capture(lead, key, value, provenance) -> None:
        db.add(CapturedField(lead_id=lead.id, key=key, value=value, provenance=provenance))

    # --- Devon Clarke: booked overnight, confirmation email unanswered --------
    devon = add_lead("Devon Clarke", "devon.clarke@example.com", "(319) 555-0188",
                     "chat", marcus, 9)
    devon_slot = _next_open_slot(hours, now, 1, 10)
    devon_day = devon_slot.strftime("%A")
    devon_convo = add_convo(devon, "chat", "booked", [
        ("buyer", "Do you have anything with a third row under $20k?"),
        ("assistant", f"We do -- a {sienna.year} {sienna.make} {sienna.model} {sienna.trim} at "
                      f"${sienna.price:,}, {sienna.mileage:,} miles, eight seats."),
        ("buyer", "That could work. I need something by the end of the month."),
        ("assistant", f"I can get you in {devon_day} at 10:00 AM or {devon_day} at 2:30 PM."),
        ("buyer", f"{devon_day} at 10 works. Devon Clarke, devon.clarke@example.com."),
        ("assistant", f"Booked -- {devon_day} 10:00 AM to see the Sienna. "
                      "Confirmation is on its way."),
    ], 9)
    capture(devon, "budget", "under $20k", "typed")
    capture(devon, "timeline", "end of the month", "typed")
    capture(devon, "seats_needed", "third row", "typed")
    capture(devon, "financing", "likely financing", "inferred")
    db.add(VehicleMention(conversation_id=devon_convo.id, vehicle_id=sienna.id,
                          quoted_price=sienna.price))
    devon_appt = Appointment(
        lead_id=devon.id, vehicle_id=sienna.id, assigned_user_id=marcus.id,
        starts_at=devon_slot,
        duration_min=30, status="confirmed", booked_by="liner",
        conversation_id=devon_convo.id, created_at=now - timedelta(hours=9),
    )
    db.add(devon_appt)
    db.flush()
    db.add(Outreach(
        appointment_id=devon_appt.id, lead_id=devon.id, sent_by_user_id=marcus.id,
        channel="email", to_address=devon.email,
        subject=f"Your {devon_day} 10:00 AM appointment at Riverside Auto",
        body=f"Hi Devon -- you're set for {devon_day} at 10:00 AM to see the 2018 Sienna.",
        provider="outbox", provider_message_id="outbox-seed-devon", status="sent",
        sent_at=now - timedelta(hours=7), created_at=now - timedelta(hours=7),
    ))

    # --- Janet Whitfield: booked, unconfirmed, unassigned --------------------
    janet = add_lead("Janet Whitfield", "j.whitfield@example.com", "", "chat", None, 6)
    janet_slot = _next_open_slot(hours, now, 2, 16)
    janet_day = janet_slot.strftime("%A")
    janet_convo = add_convo(janet, "chat", "booked", [
        ("buyer", "I'm looking at trucks for towing a small camper."),
        ("assistant", f"The {f150.year} {f150.make} {f150.model} {f150.trim} is ${f150.price:,} "
                      f"with {f150.mileage:,} miles -- crew cab, four wheel drive."),
        ("buyer", f"Can I look at it {janet_day} afternoon?"),
        ("assistant", f"{janet_day} at 4:00 PM or {janet_day} at 5:30 PM -- which suits you?"),
        ("buyer", "4 PM. Janet Whitfield, j.whitfield@example.com."),
        ("assistant", f"You're booked for {janet_day} at 4:00 PM."),
    ], 6)
    capture(janet, "use_case", "towing a small camper", "typed")
    capture(janet, "vehicle_interest", f"{f150.year} {f150.make} {f150.model}", "listing")
    capture(janet, "budget", "around $28k", "inferred")
    db.add(VehicleMention(conversation_id=janet_convo.id, vehicle_id=f150.id,
                          quoted_price=f150.price))
    db.add(Appointment(
        lead_id=janet.id, vehicle_id=f150.id, assigned_user_id=None,
        starts_at=janet_slot,
        duration_min=30, status="booked", booked_by="liner",
        conversation_id=janet_convo.id, created_at=now - timedelta(hours=6),
    ))

    # --- Gil Otonye: escalated on out-the-door price, agent holding ----------
    gil = add_lead("Gil Otonye", "gil.otonye@example.com", "(319) 555-0155", "chat", None, 2)
    gil_convo = add_convo(gil, "chat", "escalated", [
        ("buyer", "What's the out-the-door price on the Accord?"),
        ("assistant", "That's a question for one of our people -- I've asked a rep to jump in "
                      "with the exact number including tax, title and fees."),
    ], 2, status="handoff", paused=True, focus=accord)
    capture(gil, "vehicle_interest", f"{accord.year} {accord.make} {accord.model}", "listing")
    capture(gil, "buying_signal", "asked for out-the-door price", "inferred")
    db.add(VehicleMention(conversation_id=gil_convo.id, vehicle_id=accord.id,
                          quoted_price=accord.price))
    otd_rule = db.query(HandoffRule).filter_by(key="out_the_door_price").one()
    db.add(Escalation(
        conversation_id=gil_convo.id, handoff_rule_id=otd_rule.id,
        reason="Buyer asked for an out-the-door price on the 2020 Accord.",
        created_at=now - timedelta(hours=2),
    ))

    # --- Amara Osei: qualified, still browsing, no appointment yet -----------
    amara = add_lead("Amara Osei", "amara.osei@example.com", "", "website", priya, 20)
    amara_convo = add_convo(amara, "chat", "qualifying", [
        ("buyer", "I need something reliable for a 40 mile commute."),
        ("assistant", f"The {civic.year} {civic.make} {civic.model} at ${civic.price:,} is the "
                      f"value pick, {civic.mileage:,} miles."),
        ("buyer", "I'd be financing rather than paying cash."),
        ("assistant", "Understood -- a rep handles financing in person. When could you come in?"),
    ], 20, status="active")
    capture(amara, "use_case", "40 mile commute", "typed")
    capture(amara, "financing", "financing rather than paying cash", "typed")
    capture(amara, "budget", "under $15k", "inferred")
    db.add(VehicleMention(conversation_id=amara_convo.id, vehicle_id=civic.id,
                          quoted_price=civic.price))

    # --- Sol Bergman: phone lead with no email. Unreachable by the product ---
    sol = add_lead("Sol Bergman", "", "(319) 555-0173", "phone", None, 14)
    add_convo(sol, "voice", "vehicle_focus", [
        ("buyer", "Calling about the minivan you have listed."),
        ("assistant", "Happy to help. Can I get an email so I can send you the details?"),
        ("buyer", "I'd rather not."),
        ("assistant", "No problem -- I'll have a rep call you back."),
    ], 14)
    capture(sol, "phone", "(319) 555-0173", "caller_id")
    capture(sol, "vehicle_interest", "minivan", "typed")

    # --- Tara Nolan: sold-vehicle blast radius ------------------------------
    tara = add_lead("Tara Nolan", "tara.nolan@example.com", "", "chat", trevor, 30)
    tara_convo = add_convo(tara, "chat", "vehicle_focus", [
        ("buyer", "Is the Pacifica still available?"),
        ("assistant", f"The {pacifica.year} {pacifica.make} {pacifica.model} is listed at "
                      f"${pacifica.price:,} with {pacifica.mileage:,} miles."),
    ], 30)
    capture(tara, "vehicle_interest", f"{pacifica.year} {pacifica.make} {pacifica.model}", "listing")
    db.add(VehicleMention(conversation_id=tara_convo.id, vehicle_id=pacifica.id,
                          quoted_price=pacifica.price))
    db.add(VehicleMention(conversation_id=devon_convo.id, vehicle_id=pacifica.id,
                          quoted_price=pacifica.price))

    db.commit()


def seed(db: Session | None = None) -> None:
    create_all()
    owns_session = db is None
    db = db or SessionLocal()
    try:
        _clear(db)
        _seed_dealership(db)
        users = _seed_users(db)
        vehicles = _seed_vehicles(db)
        _seed_settings(db, users[0])
        _seed_rules_and_knowledge(db)
        _seed_rails(db)
        _seed_history(db, users, vehicles)
        print(
            f"Seeded {db.query(Vehicle).count()} vehicles, {db.query(Lead).count()} leads, "
            f"{db.query(Conversation).count()} conversations, "
            f"{db.query(Appointment).count()} appointments, "
            f"{db.query(Rail).count()} rails."
        )
        print(
            f"Manager: dana.mercer@example.invalid / {settings.manager_password}\n"
            f"Rep:     marcus.vale@example.invalid / {settings.rep_password}"
        )
    finally:
        if owns_session:
            db.close()


if __name__ == "__main__":
    seed()
