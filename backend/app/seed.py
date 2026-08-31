"""Riverside Auto fixture.

Reproduces the dataset the mockups describe (§18.1): a fully populated
"yesterday" so no dashboard screen is ever empty on first run. Everything here
is a real row read by the real agent -- narrow, not fake.

Idempotent by wipe: ``seed()`` clears the tables it owns and rebuilds them.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta

import yaml
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app import profile
from app.add_user import initials
from app.config import settings
from app.db import SessionLocal, create_all, utcnow
from app.models import (
    Appointment,
    AssistantSettings,
    CallBuyerTrack,
    CallRecording,
    CallSegment,
    CallUsage,
    CapturedField,
    Conversation,
    Dealership,
    EmailReplyDue,
    Escalation,
    Event,
    HandoffRule,
    InboundEmail,
    IngestRun,
    KnowledgeEntry,
    Lead,
    LeadAddress,
    Message,
    OpsUser,
    Outreach,
    Rail,
    RuntimeFlag,
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
    if role == "owner":
        return settings.owner_password
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
    ("Test drives", "Test drives are walk-in or booked, and you'll need a valid licence "
     "and proof of insurance. Allow about 30 minutes."),
    ("Vehicle history", "Every vehicle comes with a free CarFax report, and we'll print "
     "it for you before you sign anything."),
    ("Inspection", "Every vehicle passes a 120-point inspection before it goes on the "
     "lot. You're also welcome to have your own mechanic look at it."),
    ("Returns", "There's a 3-day/300-mile exchange on every vehicle -- swap it for "
     "something else on the lot, no restocking fee."),
    ("Delivery", "We deliver within 100 miles of Cedar Falls for $199, or free on "
     "vehicles over $20,000. Beyond that we can arrange transport at cost."),
    ("Payment methods", "Cash, cashier's cheque, or financing through one of our "
     "lenders. We don't take personal cheques or more than $5,000 in cash."),
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
#: (kind, stage, label, message_text, advances_to, sort_order, requires_vehicle,
#:  action)
#:
#: `action` is what makes a chip answer itself: the tool runs, the sentence is
#: built from its result, and no model turn happens. It is only ever set where
#: the chip's meaning is fixed by whoever wrote it -- "What's under $20k?" can
#: only mean one search. A chip with no action is read by the model like any
#: other buyer message, which is every chip that asks something open.
RAILS = [
    ("opener", "opening", "What's under $20k?", "What do you have under $20,000?",
     "browsing", 1, False, {"do": "under_price", "args": {"max_price": 20000}}),
    ("opener", "opening", "Anything with a third row?",
     "I need something with a third row for the kids.", "browsing", 2, False,
     {"do": "with_seats", "args": {"min_seats": 7}}),
    ("opener", "opening", "Something reliable for commuting",
     "I'm after something reliable for a daily commute.", "browsing", 3, False,
     {"do": "matching", "args": {"keywords": "commuter reliable fuel efficient",
                                 "lead_in": "Here's what I'd put you in for a daily commute:"}}),

    # No action: "the first one" is a reference into the conversation, and
    # resolving a reference is the model's job rather than a fixed search.
    ("followup", "browsing", "Tell me about the first one",
     "Tell me more about the first one.", "vehicle_focus", 1, False, None),
    ("followup", "browsing", "Anything cheaper?",
     "Do you have anything cheaper than those?", "browsing", 2, False,
     {"do": "cheaper", "args": {}}),
    ("followup", "browsing", "Lower mileage options?",
     "Do you have anything with lower mileage?", "browsing", 3, False,
     {"do": "fewer_miles", "args": {}}),

    ("followup", "vehicle_focus", "Can I see it this week?",
     "Can I come see it this week?", "slot_offered", 1, True, None),
    ("followup", "vehicle_focus", "Is the price negotiable?",
     "Is the price negotiable on that one?", "objection", 2, True, None),
    ("followup", "vehicle_focus", "How many miles on it?",
     "How many miles does it have?", "vehicle_focus", 3, True, None),

    ("followup", "objection", "That works for me",
     "Okay, that works for me.", "qualifying", 1, False, None),
    ("followup", "objection", "I'd have a trade-in",
     "I'd want to trade in my current car.", "qualifying", 2, False, None),

    ("followup", "qualifying", "Looking to buy in a couple weeks",
     "I'm hoping to buy in the next couple of weeks.", "slot_offered", 1, False, None),
    ("followup", "qualifying", "I'd be financing",
     "I'd be financing rather than paying cash.", "qualifying", 2, False, None),

    ("followup", "slot_offered", "Saturday morning works",
     "Saturday morning works for me.", "contact_capture", 1, False, None),
    ("followup", "slot_offered", "Anything later in the day?",
     "Do you have anything later in the day?", "slot_offered", 2, False, None),

    # No chip at contact_capture, deliberately. There was one -- "Send it to my
    # email" -- and its message_text was a fixture buyer's name and address:
    # "I'm Jordan Reyes, and my email is jordan.reyes@example.com." A real
    # buyer tapping it told Liner they were somebody else, and
    # `save_captured_fields` recorded that as `typed`, which is the provenance
    # meaning *the buyer said this*. A rep then rings Jordan Reyes.
    #
    # Nothing pre-writable belongs here: the assistant has just asked for a
    # name and an email, and a chip cannot know either. The composer is the
    # answer, and the booking card asks for the same fields properly.

    ("followup", "booked", "What should I bring?",
     "What should I bring with me?", "booked", 1, False, None),

    ("knowledge", "", "What's your doc fee?", "What's your doc fee?", "", 1, False, None),
    ("knowledge", "", "Do you take trade-ins?", "Do you take trade-ins?", "", 2, False, None),
]


def _clear(db: Session) -> None:
    """Empty the dealership's tables, in an order the foreign keys allow.

    `ops_users` and `ops_demo_requests` are ours and are deliberately absent:
    rebuilding a showroom fixture must not throw away demos real people booked
    with us. `make reset-db` deletes the whole file and does lose them, which
    is why `make reset-dealership` exists for a box with anything real on it.

    **The list has to be complete or the reseed fails outright.** Four call
    tables and `inbound_emails` were added after this was written and none of
    them was added here, so on any database that had taken a call or received
    a reply -- which is every box a demo has been rehearsed on -- reseeding
    died on `DELETE FROM outreach` with a bare `FOREIGN KEY constraint
    failed` and no clue which table. It stayed invisible because a fresh
    `make reset-db` deletes the file first and never reaches this.
    """
    # A call's recording, its cost rows and its speech marks belong to the
    # conversation and go with it. The audio files under backend/var/ are not
    # touched here -- they are named by row id, so a stale one is orphaned
    # rather than served to the wrong buyer.
    for model in (
        CallSegment, CallUsage, CallBuyerTrack, CallRecording,
        VehicleMention, Outreach, Escalation, Appointment, CapturedField, Message,
        EmailReplyDue, LeadAddress, RuntimeFlag,
        Conversation, Lead, IngestRun, Vehicle, Rail, KnowledgeEntry, HandoffRule,
        AssistantSettings, User, Dealership, Event,
    ):
        db.query(model).delete()
    db.commit()


def _unplace_inbound(db: Session) -> None:
    """Detach delivery receipts from the dealership, without destroying them.

    `inbound_emails` is the one table caught between the two halves of the
    split. It points at `leads` and `outreach`, so it has to be dealt with
    before those are emptied -- but it is a record that somebody really wrote
    in, and an unresolved delivery is listed in *our* mailbox at `/ops`,
    because a stranger who mails `support@` has no buyer page anywhere else.
    Deleting it on a reseed is the same failure `_clear` avoids for the `ops_`
    tables: mail thrown away on somebody's behalf.

    So the row survives and only the pointers go. The envelope, the body and
    `outcome` are what actually happened and are left exactly as they were --
    rewriting a receipt to say something other than what occurred is the one
    thing a receipt must never do.

    Run before `_clear`, which is the only ordering that works: afterwards the
    rows it would have detached are already the reason the delete failed.
    """
    stale = (
        db.query(InboundEmail)
        .filter(InboundEmail.lead_id.isnot(None) | InboundEmail.outreach_id.isnot(None))
        .all()
    )
    for row in stale:
        row.lead_id = None
        row.outreach_id = None
    if stale:
        db.commit()
        print(f"  kept {len(stale)} inbound email receipt(s), detached from the old data")


#: What a profile has to say before anything is built from it. Hours are
#: separate below, because "closed every day" is a legitimate shape for the
#: file and a meaningless one for a showroom.
REQUIRED_PROFILE_FIELDS = ("name", "timezone", "address", "phone")


def _has_fixture(raw: dict) -> bool:
    """Whether this profile carries the made-up showroom, or only itself.

    Everything below the dealership row -- fourteen curated vehicles, the
    sample CSV lot, a populated yesterday of leads and conversations, and a
    dozen written policy answers -- is **Riverside Auto's**, invented so that
    no dashboard screen is ever empty on first run and so the smoke test has a
    "third row" to search for.

    Seeded into a prospect's instance it is not a head start, it is wrong
    data: their buyer searches the lot and Liner offers a Toyota Sienna from a
    showroom in Iowa, and asks about the doc fee and gets $189 because a
    fixture said so. Their cars come from a crawl of their own site and their
    policies come from them.

    Opt in rather than out, and stated in the profile rather than derived from
    its filename: a prospect file copied from riverside.yaml would otherwise
    inherit the fixture through a name check nobody thought about.
    """
    return bool(raw.get("showroom_fixture"))


def _check_profile(raw: dict, path) -> None:
    """Refuse a profile that is still a template.

    A prospect's real details cannot be reached from here, so a half-filled
    file is the expected state of a new one -- and seeding from it would mint
    a dealership with a blank address and no phone number, which every surface
    would then print at a buyer. Worse would be filling the gaps with
    something plausible: an invented address survives a demo and gets repeated
    back to a customer.

    So this fails, and names what is missing rather than saying "invalid".
    """
    missing = [f for f in REQUIRED_PROFILE_FIELDS if not str(raw.get(f) or "").strip()]
    hours = raw.get("hours") or {}
    if not any(hours.values()):
        missing.append("hours (every day is null, so the calendar has no slots)")
    if missing:
        raise SystemExit(
            f"\n{path} is not filled in yet.\n\n"
            "  Missing: " + ", ".join(missing) + "\n\n"
            "These are that dealership's real details and nothing here can look\n"
            "them up. Fill them in from their own site, then seed again.\n"
        )


def load_profile() -> dict:
    """The YAML this instance is running as, or a message naming the choices."""
    path = settings.dealership_config
    if not path.is_file():
        available = sorted(p.stem for p in settings.dealership_dir.glob("*.yaml"))
        raise SystemExit(
            f"\nNo dealership profile at {path}.\n\n"
            + ("  Available: " + ", ".join(available) + "\n" if available else "")
            + "  Pick one with DEALERSHIP=<name>, or leave it unset for the default.\n"
        )
    raw = yaml.safe_load(path.read_text()) or {}
    _check_profile(raw, path)
    return raw


def _seed_dealership(db: Session, raw: dict) -> Dealership:
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


#: Us, not the dealership -- and in our own table, `ops_users`. Sharing
#: `users` and separating on a role string meant every unfiltered query(User)
#: was a place we could surface inside somebody else's showroom, and three of
#: them did.
#:
#: `password_env` is the `.env` key each account is seeded from, stored on the
#: row rather than derived from the address: a third person is a row plus one
#: line of `.env` and no code. Each falls back to OWNER_PASSWORD when its own
#: key is unset, so an install written before the split keeps working.
#:
#: Module level so `make add-owners` can put them on a database seeded before
#: any of this existed, without the reseed that would take the leads with it.
OWNERS = [
    ("Liner Founder", "founder@linerai.us", "FOUNDER_PASSWORD", "LF"),
    ("Liner CTO", "cto@linerai.us", "CTO_PASSWORD", "LC"),
]

STAFF = [
    ("Dana Mercer", "dana.mercer@example.invalid", "manager", "DM", 6),
    ("Marcus Vale", "marcus.vale@example.invalid", "rep", "MV", 8),
    ("Priya Raman", "priya.raman@example.invalid", "rep", "PR", 8),
    ("Trevor Osei", "trevor.osei@example.invalid", "rep", "TO", 8),
]


def build_user(name: str, email: str, role: str, initials: str, cap: int) -> User:
    return User(
        name=name, email=email, password_hash=_hash(_password_for(role)),
        role=role, avatar_initials=initials, daily_cap=cap,
        notify_channel="email" if role == "manager" else "dashboard",
    )


def build_owner(name: str, email: str, password_env: str, initials: str) -> OpsUser:
    return OpsUser(
        name=name, email=email, avatar_initials=initials, password_env=password_env,
        password_hash=_hash(settings.password_for_ops(password_env.lower())),
    )


def _seed_users(db: Session, raw: dict) -> tuple[list[User], list[tuple[User, str]]]:
    """The dealership's staff, and the password each one can sign in with.

    Two lists come back because the caller prints them: the users, and
    `(user, password)` for every account created. **The seed must report the
    accounts it actually made** -- it used to print Dana Mercer and Marcus Vale
    unconditionally, so a profile seeded with its own staff ended with two
    logins that did not exist. Somebody reads that line, types it, and gets a
    401 that -- correctly, since the login form must never confirm whether an
    address exists -- tells them nothing.

    A prospect's instance should not ship with Dana Mercer on the roster
    anyway: invented names in every assignment picker, on the team page their
    real manager is reading, is the same failure as being greeted as Riverside
    Auto. So a profile with a `staff:` list gets exactly that list.

    The fixture's four keep their `.env`-driven passwords, because every test,
    screenshot and smoke run signs in as one of them. A profile's own people
    get a generated one, for the reason `add_user` generates rather than
    reading `.env`: an environment variable per person is one somebody has to
    add to the deployment. A reseed rebuilds the database, so it rotates them.
    """
    people = profile.staff() if not _has_fixture(raw) else []
    if not people:
        users = [build_user(*person) for person in STAFF]
        db.add_all(users)
        db.commit()
        return users, [(u, _password_for(u.role)) for u in users]

    users, minted = [], []
    for person in people:
        password = secrets.token_urlsafe(12)
        user = User(
            name=person["name"], email=person["email"], role=person["role"],
            password_hash=_hash(password),
            avatar_initials=initials(person["name"]),
            active=True, daily_cap=6 if person["role"] == "manager" else 8,
        )
        users.append(user)
        minted.append((user, password))
    db.add_all(users)
    db.commit()
    return users, minted


def _seed_owners(db: Session) -> int:
    """Ours, and idempotent -- `_clear` never touches `ops_users`.

    Re-hashing an existing row would undo a password somebody set with
    `make set-password`, and a reseed of the dealership's fixture is no reason
    to lock one of us out.
    """
    added = 0
    for person in OWNERS:
        if db.query(OpsUser).filter_by(email=person[1]).first() is None:
            db.add(build_owner(*person))
            added += 1
    db.commit()
    return added


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


def _seed_csv_inventory(db: Session) -> None:
    """Load the sample lot on top of the curated fixtures, if it is present.

    Deliberately additive. The fourteen hand-written vehicles carry the keywords
    the rails and the smoke test depend on ("third row", the do-not-discuss
    BMW), so replacing them would break the scripted flow. These add volume and
    variety on top -- what a real lot looks like when you search it.

    It goes through import_csv + publish rather than a second insert path, so
    the sample data proves the importer works and appears in the import history
    like any other run.
    """
    if not settings.inventory_csv.is_file():
        return
    from app.ingest.csv_import import import_csv
    from app.ingest.pipeline import publish

    run = import_csv(db, settings.inventory_csv.read_text(encoding="utf-8-sig"))
    if run.status != "ready":
        print(f"  inventory CSV not loaded: {run.status}")
        return
    applied = publish(db, run)
    print(f"  loaded {applied['created']} vehicles from {settings.inventory_csv.name}")


def _possessive(name: str) -> str:
    """`Riverside Auto` -> `Riverside Auto's`; `Craig and Landreth Cars` -> `... Cars'`.

    A detail, and the first line the buyer reads. "Craig and Landreth Cars's
    assistant" is the sort of thing that makes a demo look assembled rather
    than built, and it is the sentence the whole conversation opens on.
    """
    name = (name or "").strip()
    return f"{name}'" if name.endswith(("s", "S")) else f"{name}'s"


def _seed_profile_inventory(db: Session) -> None:
    """A real dealership's own lot, from a file in the repository.

    The crawl is the intended source and this is what to do when it cannot
    run: a dealer whose site refuses our agent, or a network that cannot reach
    it. Somebody exports the lot once and it is committed, so `make reset-db`
    rebuilds a real 486-car showroom with no network at all.

    It goes through `import_csv` and `publish` -- the same two functions a
    dealer's own upload goes through -- rather than inserting rows directly.
    A second insert path is how one of them stops dropping the cost columns.
    """
    from app.profile import inventory

    name = (inventory().get("fixture_csv") or "").strip()
    if not name:
        return
    path = settings.fixtures_dir / name
    if not path.is_file():
        print(f"  inventory fixture not found: {path}")
        return

    from app.ingest.csv_import import import_csv
    from app.ingest.pipeline import publish

    run = import_csv(db, path.read_text(encoding="utf-8-sig"))
    if run.status != "ready":
        print(f"  inventory fixture not loaded: {run.status}")
        return
    applied = publish(db, run)
    errors = json.loads(run.errors_json or "[]")
    print(f"  loaded {applied['created']} vehicles from {name}")
    for entry in errors[:3]:
        print(f"    note: {entry.get('error')}")


def _seed_settings(db: Session, manager: User, raw: dict) -> None:
    """The published instructions, greeting included.

    The greeting names the dealership because the buyer reads it, and it is
    also quoted back to the model -- `prompts.py` tells it the greeting has
    already been sent, so a hardcoded one would have the assistant believe it
    had introduced itself as somebody else's showroom.

    `credit_application_url` is the fixture's alone. A real prospect has not
    given us their finance portal, and inventing one puts a link in front of a
    buyer that goes nowhere: with it empty the draft refuses with a typed
    `not_configured` and the overview card says why, which is the behaviour
    that exists for exactly this.
    """
    greeting = f"Hi! I'm Liner, {_possessive(raw['name'])} assistant. What are you looking for?"
    # .example is reserved by RFC 2606, the same reason the seeded addresses
    # use .invalid: this is visibly a fixture and cannot resolve to a real
    # finance portal.
    finance = "https://riversideauto.example/finance" if _has_fixture(raw) else ""
    live = AssistantSettings(
        version=7, status="live", tone="warm", push_level="balanced",
        price_mode="listed_only", financing_mode="refer_to_rep",
        after_hours_mode="full_service", booking_slot_length=30,
        credit_application_url=finance, greeting=greeting,
        published_by=manager.id, published_at=utcnow() - timedelta(days=9),
    )
    draft = AssistantSettings(
        version=8, status="draft", tone="warm", push_level="assertive",
        price_mode="listed_only", financing_mode="refer_to_rep",
        after_hours_mode="full_service", booking_slot_length=30,
        credit_application_url=finance, greeting=greeting,
    )
    db.add_all([live, draft])
    db.commit()


def _knowledge_for(raw: dict) -> list[tuple[str, str]]:
    """What this dealership has actually told us, and nothing else.

    Policy answers are returned verbatim to a buyer and never composed --
    that is the whole reason `answer_from_knowledge` returns `found: false`
    rather than a near miss. Riverside's doc fee, deposit and return policy
    are invented for a fixture, so handing them to a prospect's instance would
    have Liner quote a real buyer a $189 doc fee and a 3-day exchange that
    nobody at that dealership has ever agreed to. A wrong policy is worse than
    no policy, so a profile gets its own list or gets none.
    """
    if _has_fixture(raw):
        return list(KNOWLEDGE)
    entries = []
    for item in raw.get("knowledge") or []:
        topic = str((item or {}).get("topic") or "").strip()
        answer = str((item or {}).get("answer") or "").strip()
        if topic and answer:
            entries.append((topic, answer))
    return entries


def _seed_rules_and_knowledge(db: Session, raw: dict) -> None:
    fired = {"out_the_door_price": 12, "financing_trouble": 5, "asks_for_manager": 3,
             "urgency": 8, "ready_to_sign": 4}
    for key, label, description, threshold, unit, route in HANDOFF_RULES:
        db.add(HandoffRule(
            key=key, label=label, description=description, enabled=True,
            threshold_value=threshold, threshold_unit=unit, route_target=route,
            notify="email_dashboard", fired_count=fired.get(key, 0),
        ))
    for topic, answer in _knowledge_for(raw):
        db.add(KnowledgeEntry(topic=topic, answer=answer, use_count=0))
    db.commit()


def _seed_rails(db: Session) -> None:
    """The buyer's chips. A knowledge chip is dropped when nothing answers it.

    "What's your doc fee?" is a promise that pressing it produces the doc fee.
    With no entry behind it the buyer taps and Liner says it will have to
    check -- which is honest, and is still a chip the dealership put on screen
    asking a question it cannot answer. Offering it only where there is an
    answer is the same rule as the channel strip: never advertise a capability
    that is not there.
    """
    knowledge = {k.topic: k for k in db.query(KnowledgeEntry).all()}
    topic_for = {"What's your doc fee?": "Doc fee", "Do you take trade-ins?": "Trade-ins"}
    for kind, stage, label, text, advances, order, needs_vehicle, action in RAILS:
        topic = topic_for.get(label, "")
        entry = knowledge.get(topic)
        if topic and entry is None:
            continue
        db.add(Rail(
            kind=kind, stage=stage, label=label, message_text=text,
            advances_to=advances, sort_order=order, requires_vehicle=needs_vehicle,
            knowledge_entry_id=entry.id if entry else None, enabled=True,
            action_json=json.dumps(action) if action else "",
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
    # The dealership's own staff. Liner's two `owner` accounts are in the same
    # table and must not be here: this history assigns leads and appointments,
    # and an owner cannot own a buyer at a showroom they do not work for.
    manager, marcus, priya, trevor = [u for u in users if u.role in ("manager", "rep")]
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
        # After the turns, not at the start: the timeline is ordered by time,
        # and an appointment stamped when the thread opened draws above the
        # message that booked it.
        conversation_id=devon_convo.id,
        created_at=now - timedelta(hours=9) + timedelta(minutes=5),
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

    # Devon rings back the next morning. The same buyer, a second channel, and
    # the reason the dashboard is organised by person rather than by thread: on
    # a per-thread list this is a stranger with no booking, and a rep reading it
    # would offer them a slot they already have.
    add_convo(devon, "voice", "vehicle_focus", [
        ("buyer", "It's Devon -- I booked online last night about the Sienna."),
        ("assistant", f"You're down for {devon_day} at 10:00 AM with Marcus. "
                      "Anything you'd like ready before you get here?"),
        ("buyer", "Could you have the third row folded down so I can see the space?"),
        ("assistant", "I'll pass that to Marcus so it's set up when you arrive."),
    ], 7, status="closed")

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
        conversation_id=janet_convo.id,
        created_at=now - timedelta(hours=6) + timedelta(minutes=5),
    ))

    # --- Gil Otonye: escalated on out-the-door price, agent holding ----------
    gil = add_lead("Gil Otonye", "gil.otonye@example.com", "(319) 555-0155", "chat", None, 2)
    gil_convo = add_convo(gil, "chat", "escalated", [
        ("buyer", "What's the out-the-door price on the Accord?"),
        ("assistant", "That's a question for one of our people -- I've asked a rep to jump in "
                      "with the exact number including tax, title and fees."),
    ], 1.5, status="handoff", paused=True, focus=accord)
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
    ], 0.6, status="active")
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

    # Two finance applications a rep sent. Real outreach rows, counted by the
    # same query as anything a rep sends today -- the overview card is not
    # given a number, it adds these up. Without them the card reads zero on a
    # fresh install and a working feature looks like a broken one.
    # One of the two was opened. The card counts opens, so a fixture where
    # every send was clicked would make the difference between "we sent it" and
    # "they looked at it" invisible -- which is the whole reason it counts opens.
    for lead, rep, hours_ago, clicks in ((janet, marcus, 5, 2), (amara, priya, 19, 0)):
        token = f"seed{lead.id[:12].replace('-', '')}"
        db.add(Outreach(
            lead_id=lead.id, sent_by_user_id=rep.id, channel="email",
            kind="credit_application", to_address=lead.email,
            subject="Finance application -- Riverside Auto",
            body=(
                f"Hi {lead.name.split()[0]},\n\nHere is our finance application, which "
                f"you can fill in before you come in so we are not doing paperwork while "
                f"you are here:\n\n/r/{token}\n\n"
                f"Best,\n{rep.name}\nRiverside Auto"
            ),
            provider="outbox", provider_message_id=f"outbox-seed-credit-{lead.id[:8]}",
            status="sent", sent_at=now - timedelta(hours=hours_ago),
            created_at=now - timedelta(hours=hours_ago),
            click_token=token, click_count=clicks,
            first_clicked_at=now - timedelta(hours=hours_ago - 0.5) if clicks else None,
            last_clicked_at=now - timedelta(hours=hours_ago - 1) if clicks else None,
        ))

    db.commit()


def seed(db: Session | None = None) -> None:
    create_all()
    owns_session = db is None
    db = db or SessionLocal()
    try:
        raw = load_profile()
        _unplace_inbound(db)
        _clear(db)
        _seed_dealership(db, raw)
        users, logins = _seed_users(db, raw)
        if _has_fixture(raw):
            vehicles = _seed_vehicles(db)
            _seed_csv_inventory(db)
        else:
            _seed_profile_inventory(db)
        _seed_settings(db, users[0], raw)
        _seed_rules_and_knowledge(db, raw)
        _seed_rails(db)
        if _has_fixture(raw):
            _seed_history(db, users, vehicles)
        _seed_owners(db)
        print(
            f"Seeded {db.query(Vehicle).count()} vehicles, {db.query(Lead).count()} leads, "
            f"{db.query(Conversation).count()} conversations, "
            f"{db.query(Appointment).count()} appointments, "
            f"{db.query(Rail).count()} rails."
        )
        if not _has_fixture(raw):
            # Not an error and not a half-seed: this profile is a real
            # dealership, so the only rows here are the ones it actually
            # states. Said out loud because an empty lot on first run
            # otherwise reads as a seed that failed.
            print(
                f"\n{raw['name']} carries no showroom fixture, so the lot is empty and\n"
                "there is no demo history. Their cars come from their own site:\n"
                + (f"  {profile.inventory()['source_url']}\n"
                   "  Press Import on /app/inventory, review the run, then publish.\n"
                   if profile.inventory()["source_url"]
                   else "  Add an `inventory.source_url` to their profile, or import a CSV.\n")
                + ("Policy answers: none are seeded. Add a `knowledge:` list to\n"
                   f"{settings.dealership_config} with answers they have actually given,\n"
                   "or Liner says it will check rather than inventing one.\n"
                   if not _knowledge_for(raw) else "")
            )
        # The accounts that were actually created, never a hardcoded pair.
        # A generated password is shown once and only the hash is kept, so the
        # line saying how to set a new one has to be right here beside it.
        print("\nSign in with:")
        for user, password in logins:
            print(f"  {user.role:8} {user.email:38} {password}")
        # A fixture account's password comes from `.env` and survives; a
        # profile's own person gets a fresh one on every reseed, so only that
        # kind needs the line about writing it down.
        generated = [u for u, _p in logins if u.email not in {row[1] for row in STAFF}]
        if generated:
            print("\nThose passwords were generated and are shown once -- only the hash is\n"
                  "stored. A reseed makes new ones. To set one yourself:\n"
                  f"  make set-password EMAIL={generated[0].email}")
        # Ours, in ops_users, and named with the key each password comes from
        # -- otherwise the only way to know which `.env` line to change is to
        # read the seed.
        for name, email, env_key, _initials in OWNERS:
            print(f"{name:15} {email} / {settings.password_for_ops(env_key.lower())}"
                  f"  ({env_key})")
    finally:
        if owns_session:
            db.close()


def _main() -> None:
    try:
        seed()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    _main()
