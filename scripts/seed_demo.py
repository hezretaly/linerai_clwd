#!/usr/bin/env python3
"""Bulk the fixture out to something you can actually click around in.

    make seed-demo          # 50 of each
    make seed-demo N=200

`make seed` builds the Riverside Auto fixture: fourteen curated cars, a handful
of conversations, one open escalation -- small enough to reason about, which is
exactly what it is for. It is also small enough that nothing on the dashboard
looks like a working day. Pagination never triggers, the charts have three
points, and a filter that would be wrong at scale is right by accident.

So this adds volume *on top of* the fixture rather than replacing it. Run it
twice and you get twice as much; `make reset-db` takes you back to the curated
set. The curated rows keep their meaning -- the do-not-discuss BMW is still
there, `make smoke` still finds what it looks for.

Three rules make the demo data safe to leave in a database:

* **Every address ends in `.invalid`.** That is the RFC-reserved suffix that
  can never resolve, so a demo row cannot be mailed even with a real sender
  configured and `OUTBOUND_ONLY_TO=everyone`. Phone numbers are 555-01xx,
  reserved the same way.
* **Every appointment is in the past, or more than a week out.** `make smoke`
  books the next open slot and `book_appointment` refuses a clash, so filling
  this week would break the gate -- which is the one thing demo data must
  never do.
* **It is deterministic.** A fixed random seed, so two people running this get
  the same dashboard and a screenshot means something tomorrow.

Nothing here is a simulated integration. These are rows of the same kind the
product writes; no email is sent, no call is placed, and `/api/integrations`
says exactly what it said before.
"""

from __future__ import annotations

import json
import pathlib
import random
import sys
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from app.db import SessionLocal, utcnow  # noqa: E402
from app.models import (  # noqa: E402
    Appointment,
    CallSegment,
    CallUsage,
    CapturedField,
    Conversation,
    Dealership,
    Escalation,
    InboundEmail,
    Lead,
    Message,
    Outreach,
    User,
    Vehicle,
    VehicleMention,
)
from app.seed import _next_open_slot  # noqa: E402

FIRST = [
    "Avery", "Bailey", "Cameron", "Dakota", "Elliot", "Finley", "Gray", "Harper",
    "Indigo", "Jordan", "Kai", "Logan", "Marlow", "Noel", "Oakley", "Parker",
    "Quinn", "Reese", "Sage", "Tatum", "Umi", "Vale", "Wren", "Xan", "Yuki", "Zion",
    "Amara", "Beatriz", "Carlos", "Diane", "Ephraim", "Fatima", "Gus", "Hannah",
    "Ibrahim", "Jules", "Keiko", "Lucia", "Mateo", "Nadia", "Omar", "Priya",
]
LAST = [
    "Alvarez", "Brennan", "Cho", "Delgado", "Ellery", "Fontaine", "Guzman",
    "Haddad", "Ibarra", "Jensen", "Kowalski", "Lindqvist", "Moreau", "Nakamura",
    "Okonkwo", "Pereira", "Quist", "Rasmussen", "Sandoval", "Tremblay",
]

class Need:
    """One buyer's actual reason for being here, and everything that follows
    from it.

    The point of this class is that the fields on a demo buyer used to be
    drawn independently: a want, a car, a budget, a question, a reply, an
    escalation reason and an email subject, each picked at random from its own
    list. So a buyer asking for a hybrid was shown a pickup, told "it's been
    on the lot two weeks" when they had asked about the doc fee, and mailed
    about a third row they never mentioned. Every row was individually
    plausible and the buyer, read as a whole, was nonsense -- which is worse
    than obviously fake data, because it looks fine until you click one.

    So the want picks the car (`fits` runs against real inventory), the car
    fixes the budget, and every line either side is written against both.
    """

    def __init__(self, key, want, field, fits, asks):
        self.key = key
        self.want = want
        #: (key, value) captured against the lead -- the thing they said.
        self.field = field
        self.fits = fits
        #: Questions this buyer plausibly asks, each paired with the answer.
        self.asks = asks


def _feature(vehicle, *words) -> bool:
    blob = (vehicle.features_json or "").lower() + " " + (vehicle.keywords or "").lower()
    blob += f" {vehicle.make} {vehicle.model} {vehicle.trim}".lower()
    return any(w in blob for w in words)


#: Answers that can be written against any car, so they are shared. Each is a
#: format string over the vehicle -- which is what makes the reply about the
#: car actually being discussed rather than a generic line.
MILEAGE = "It's showing {mileage:,} miles."
PRICE = "It's listed at ${price:,}. A manager can talk numbers once you're in."
TITLE = "Clean title, one owner on the report."
TRADE = "Our used car manager does valuations -- want me to have them call you?"
DOCFEE = "Let me give you our own answer on that rather than guess at it."
OTD = "That's a real numbers conversation -- let me get you with a manager."
FINANCE = "We work with a few lenders. I'll have someone go through it with you."
SEEIT = "Let me check what's open this week."

NEEDS = [
    Need(
        "third_row", "third row for the school run", ("seats_needed", "third row"),
        lambda v: (v.seats or 0) >= 7 or v.body_style.lower() in {"minivan", "van"},
        [("Will three car seats fit across the back?",
          "The {model} seats {seats} -- worth trying your seats in it when you come."),
         ("Can I see it this weekend?", SEEIT),
         ("What's the mileage on that one?", MILEAGE)],
    ),
    Need(
        "budget", "something under twenty grand", ("budget", "under $20k"),
        lambda v: (v.price or 0) <= 20000,
        [("Is the price negotiable at all?", PRICE),
         ("Is that the out-the-door price?", OTD),
         ("What's the doc fee here?", DOCFEE)],
    ),
    Need(
        "awd", "an SUV with all-wheel drive", ("must_have", "all-wheel drive"),
        lambda v: v.body_style.lower() == "suv"
        and _feature(v, "all-wheel", "awd", "4wd", "four-wheel", "quattro", "4matic"),
        [("How is it in snow?",
          "It's all-wheel drive, and the {year} {model} is a sensible winter car."),
         ("Does it have a clean title?", TITLE),
         ("Can I see it this weekend?", SEEIT)],
    ),
    Need(
        "low_miles", "low mileage, doesn't matter what badge", ("must_have", "low mileage"),
        lambda v: (v.mileage or 999999) <= 40000,
        [("What's the mileage on that one?", MILEAGE),
         ("Does it have a clean title?", TITLE),
         ("Is the price negotiable at all?", PRICE)],
    ),
    Need(
        "hybrid", "a hybrid -- the commute is killing me", ("must_have", "hybrid"),
        lambda v: _feature(v, "hybrid", "prius", "electric", "plug-in"),
        [("What sort of mileage does it get?",
          "It's the hybrid, so the commute gets a lot cheaper. {mileage:,} miles on it."),
         ("Do you offer financing in house?", FINANCE),
         ("Can I see it this weekend?", SEEIT)],
    ),
    Need(
        "first_car", "a first car for my daughter", ("budget", "under $15k"),
        lambda v: (v.price or 0) <= 15000 and (v.seats or 5) <= 5,
        [("Is it safe enough for a new driver?",
          "The {year} {model} is a sensible first car -- and it's a clean one."),
         ("Is the price negotiable at all?", PRICE),
         ("What's the doc fee here?", DOCFEE)],
    ),
    Need(
        "towing", "something that can tow a small trailer", ("must_have", "towing"),
        lambda v: v.body_style.lower() == "truck" or _feature(v, "tow"),
        [("What can it pull?",
          "The {model} is the one people buy for that. Worth checking the exact rating with a rep."),
         ("Does it have a clean title?", TITLE),
         ("Can I see it this weekend?", SEEIT)],
    ),
    Need(
        "trade", "to trade in a 2015 Civic", ("trade_in", "2015 Honda Civic"),
        lambda v: True,
        [("How much would you give me for my trade?", TRADE),
         ("Is that the out-the-door price?", OTD),
         ("What's the mileage on that one?", MILEAGE)],
    ),
]

#: Which question forces a human, and why. Written here rather than picked
#: separately so the reason on the escalation is a line that is really in the
#: thread above it -- a rep opening the queue can see what caused it.
ESCALATES = {
    "Is that the out-the-door price?": "Asked for an out-the-door price",
    "How much would you give me for my trade?": "Wants a trade valuation",
    "Do you offer financing in house?": "Financing question -- needs a person",
}


def demo_email(name: str, n: int) -> str:
    """`.invalid` is reserved by RFC 2606 and can never resolve. That is the
    point: a demo lead cannot be emailed even by a fully configured sender with
    the outbound limit lifted."""
    handle = name.lower().replace(" ", ".")
    return f"{handle}.{n}@example.invalid"


def _pick_car(vehicles, need, rng):
    """A car that actually answers what they asked for.

    Falls back to the whole lot when nothing fits -- a demo must not depend on
    the fixture containing a plug-in hybrid -- but never to a do-not-discuss
    vehicle, which `search_inventory` filters and so Liner could not have
    mentioned.
    """
    fits = [v for v in vehicles if need.fits(v)]
    return rng.choice(fits or vehicles)


def _thread(rng, need, car, channel, opening):
    """The conversation itself: what was said, in order, about this car.

    Returns (lines, asked) where `asked` is the buyer's questions, so the
    escalation and the follow-up email can be written from what was really
    said rather than drawn from a list of their own.
    """
    facts = {
        "year": car.year, "make": car.make, "model": car.model,
        "trim": car.trim or car.model,
        "price": car.price or 0, "mileage": car.mileage or 0,
        "seats": car.seats or 5,
    }
    lines = [("buyer", opening)]
    lines.append(("assistant",
        f"I have a {car.year} {car.make} {car.model} at ${car.price or 0:,} "
        f"with {car.mileage or 0:,} miles. Want me to pull the details?"
        if channel == "chat" else
        f"I've got a {car.year} {car.make} {car.model}, "
        f"{_spoken(car.price or 0)} with {car.mileage or 0:,} miles on it."))

    asked = []
    for question, answer in rng.sample(need.asks, k=rng.randint(1, len(need.asks))):
        lines.append(("buyer", question))
        lines.append(("assistant", answer.format(**facts)))
        asked.append(question)
    return lines, asked


def _spoken(price: int) -> str:
    """Numbers the way they are said out loud, since a call has no screen.

    The same rule the voice addendum gives the model, applied to the demo
    transcripts -- otherwise every call in the fixture reads "$18,950", which
    is exactly what a real call must never sound like.
    """
    if not price:
        return "no price on it yet"
    thousands, rest = divmod(price, 1000)
    return f"{thousands} thousand" if not rest else f"{thousands} {rest // 100}{rest % 100 // 10}{rest % 10}"


#: Dealerships asking *us* for a demo. Ours, not Riverside Auto's -- these are
#: the rows behind `/ops`, and they have nothing to do with a car buyer.
DEALERSHIPS = [
    ("Priya Raman", "Northgate Motors", "priya", "https://northgate-motors.invalid"),
    ("Tom Whitlock", "Whitlock Family Auto", "tom", "https://whitlockauto.invalid"),
    ("Alicia Bermudez", "Cascade Pre-Owned", "alicia", "https://cascadepreowned.invalid"),
    ("Dev Anand", "Anand Autohaus", "dev", "https://anandautohaus.invalid"),
    ("Rachel Okoye", "Lakeside Import Center", "rachel", "https://lakesideimports.invalid"),
    ("Marco Pinto", "Pinto Brothers Used Cars", "marco", "https://pintobrothers.invalid"),
]

SUPPORT_NOTES = [
    "We run two rooftops on one DMS export -- can Liner tell them apart?",
    "How long does it take to point this at our own site's inventory feed?",
    "Our nights are the busy part. Does the assistant answer after hours?",
]


def _demo_requests(db, rng, now) -> int:
    """Seed the ops dashboard: demos booked with us, and people asking for help.

    Same `.invalid` rule as everything else here -- these are prospects on
    paper only, and a configured sender must not be able to reach one.

    Slots follow the demo endpoint's own rule rather than being invented: it
    offers weekday hours from `demo_hours` for `demo_days_ahead` days, and
    refuses a time already taken. So the future ones are placed on real
    weekday hours, and most of the set is in the past -- filling the whole
    offered window would leave the booking sheet with nothing to sell.
    """
    # The wording is taken from the endpoint rather than retyped: a consent
    # record is only worth anything if it is the text the page really showed.
    from app.api.demo import CONSENT
    from app.config import settings as app_settings
    from app.models import DemoRequest

    if db.query(DemoRequest).count():
        # Ran twice. More would be fine, but the fixed cast above would repeat
        # the same six names, which reads as a bug rather than as volume.
        return 0

    hours = [int(h) for h in app_settings.demo_hours.split(",") if h.strip().isdigit()]
    if not hours:
        hours = [10, 14]

    def weekday_at(days_out: int, hour: int):
        day = (now + timedelta(days=days_out)).replace(
            hour=hour, minute=0, second=0, microsecond=0
        )
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day

    plan = [
        # (days out, hour, status, kind). Negative is the past.
        (2, hours[0], "new", "demo"),
        (3, hours[-1], "new", "demo"),
        (5, hours[len(hours) // 2], "seen", "demo"),
        (-6, hours[0], "done", "demo"),
        (-13, hours[-1], "cancelled", "demo"),
    ]

    made = 0
    for index, (days_out, hour, status, kind) in enumerate(plan):
        who, dealership, handle, url = DEALERSHIPS[index % len(DEALERSHIPS)]
        slot = weekday_at(days_out, hour)
        # Somebody booked before the slot, and before now. Taking `slot - a few
        # days` alone stamped next week's bookings in the future, which every
        # "3d ago" on the page then rendered as "just now".
        submitted = min(
            slot - timedelta(days=rng.randint(1, 4), hours=rng.randint(0, 8)),
            now - timedelta(hours=rng.randint(2, 90)),
        )
        db.add(DemoRequest(
            kind=kind, name=who, dealership=dealership,
            email=f"{handle}@{dealership.split()[0].lower()}.invalid",
            phone=f"555-01{20 + index:02d}", dealership_url=url,
            slot_at=slot, consent_at=submitted, consent_text=CONSENT,
            status=status, created_at=submitted,
        ))
        made += 1

    # Support requests have no slot, which is the whole reason the calendar
    # has a "no time picked" panel -- otherwise they would live only in mail.
    for index, note in enumerate(SUPPORT_NOTES):
        who, dealership, handle, url = DEALERSHIPS[(index + len(plan)) % len(DEALERSHIPS)]
        when = now - timedelta(days=index * 3 + 1, hours=rng.randint(0, 12))
        db.add(DemoRequest(
            kind="support", name=who, dealership=dealership,
            email=f"{handle}@{dealership.split()[0].lower()}.invalid",
            phone="", dealership_url=url, message=note,
            slot_at=None, consent_at=None, consent_text="",
            status="new" if index == 0 else "seen", created_at=when,
        ))
        made += 1

    db.commit()
    return made


def build(db, count: int) -> dict[str, int]:
    rng = random.Random(20260813 + count)
    now = utcnow()
    dealership = db.query(Dealership).first()
    if dealership is None:
        raise SystemExit("No dealership. Run `make seed` first.")
    hours = json.loads(dealership.hours_json)
    reps = db.query(User).filter_by(role="rep", active=True).all()
    # Never a do-not-discuss car: `search_inventory` filters those, so Liner
    # could not have mentioned one, and a demo row saying it did would
    # contradict the rule the executor enforces.
    vehicles = [
        v for v in db.query(Vehicle).filter_by(status="available").all() if v.rule_discuss
    ]
    if not vehicles:
        raise SystemExit("No vehicles. Run `make seed` first.")

    made = dict.fromkeys(
        ("leads", "conversations", "messages", "captured_fields", "appointments",
         "outreach", "escalations", "vehicle_mentions", "inbound_emails",
         "call_usage", "call_segments", "demo_requests"), 0,
    )

    made["demo_requests"] += _demo_requests(db, rng, now)

    for n in range(count):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        need = NEEDS[n % len(NEEDS)]
        car = _pick_car(vehicles, need, rng)
        # Spread over eight weeks so the charts have a shape and the
        # conversations list has something to sort.
        first_seen = now - timedelta(days=rng.randint(0, 56), hours=rng.randint(0, 12))

        # How many times this buyer came back. Most people appear once; the
        # interesting ones on this dashboard are the buyers who chatted at 9pm
        # and rang the next morning, and that shape only exists if some of
        # them have more than one thread.
        visits = 1 if n % 3 else rng.randint(2, 3)
        channels = ["voice" if (n + i) % 3 == 0 else "chat" for i in range(visits)]

        owner = rng.choice(reps).id if reps and n % 3 else None
        lead = Lead(
            name=name,
            # Every tenth buyer has no email on file, because that is a real
            # state the dashboard flags and an all-reachable demo hides it.
            email="" if n % 10 == 9 else demo_email(name, n),
            phone=f"+1555010{n % 100:02d}",
            # What they first arrived on, not a separate roll. A lead sourced
            # "adf" whose only thread is a website chat is a contradiction a
            # rep would notice before any of the numbers.
            source="phone" if channels[0] == "voice" else rng.choice(["chat", "chat", "website"]),
            assigned_user_id=owner,
            created_at=first_seen,
        )
        db.add(lead)
        db.flush()
        made["leads"] += 1

        asked_overall: list[str] = []
        booked_convo = None
        last_convo = None
        last_stamp = first_seen

        for visit, channel in enumerate(channels):
            started = first_seen + timedelta(days=visit, hours=rng.randint(1, 6))
            if started > now:
                started = now - timedelta(minutes=30)
            opening = (
                f"I'm after {need.want}." if visit == 0 else
                f"Hi again -- still thinking about that {car.model}."
            )
            lines, asked = _thread(rng, need, car, channel, opening)
            asked_overall += asked

            # Booked on the last visit, for most buyers. Written before the
            # conversation so the stage below can be the truth rather than a
            # guess: `stage` is what book_appointment sets, and a demo row
            # that disagrees is the bug this dashboard already fixed once.
            books = visit == len(channels) - 1 and n % 2 == 0
            flagged = any(q in ESCALATES for q in asked)

            if books:
                stage = "booked"
            elif flagged:
                stage = "escalated"
            elif any("see it" in q.lower() for q in asked):
                stage = "slot_offered"
            else:
                stage = "vehicle_focus"

            # An open thread is the exception: a queue where everything is
            # live tells a rep nothing, and neither does one where nothing is.
            open_thread = visit == len(channels) - 1 and n % 7 == 0
            ended = None if open_thread else started + timedelta(
                minutes=rng.randint(3, 14)
            )
            convo = Conversation(
                lead_id=lead.id,
                channel=channel,
                # A thread waiting on a person is 'handoff', not 'closed' --
                # the buyer has gone but a rep still owes them a call back.
                status=("active" if open_thread else "handoff" if flagged else "closed"),
                stage=stage,
                focus_vehicle_id=car.id,
                started_at=started,
                ended_at=ended,
                summary=lines[-1][1] if ended else "",
            )
            db.add(convo)
            db.flush()
            made["conversations"] += 1
            last_convo = convo
            if books:
                booked_convo = convo

            stamp = started
            offset = 0
            for role, content in lines:
                stamp += timedelta(seconds=rng.randint(20, 90))
                db.add(Message(
                    conversation_id=convo.id, role=role, content=content, created_at=stamp,
                ))
                made["messages"] += 1
                # A call's two halves are joined on a clock stamped in the
                # browser, so the demo calls carry the same marks -- otherwise
                # the transcript view has nothing to order by on the one
                # channel where arrival order is known to be wrong.
                if channel == "voice":
                    length = 1500 + len(content) * 45
                    db.add(CallSegment(
                        conversation_id=convo.id,
                        speaker=role, started_ms=offset, ended_ms=offset + length,
                        text=content,
                        source="model" if role == "assistant" else "recorded",
                        created_at=stamp,
                    ))
                    made["call_segments"] += 1
                    offset += length + rng.randint(400, 1200)
            last_stamp = stamp

            db.add(VehicleMention(
                conversation_id=convo.id, vehicle_id=car.id,
                quoted_price=car.price, created_at=started + timedelta(seconds=45),
            ))
            made["vehicle_mentions"] += 1

            if flagged:
                reason = ESCALATES[next(q for q in asked if q in ESCALATES)]
                # Claimed exactly where somebody owns the buyer -- the same
                # rule `app/escalations.py` enforces at runtime, because a
                # fixture that breaks the invariant is a bug report about the
                # product. It read `owner is not None and n % 4 == 0`, so three
                # owned buyers in four wore "Needs a person" next to the name of
                # the person who had them, and assigning somebody looked broken.
                claimed = owner is not None
                db.add(Escalation(
                    conversation_id=convo.id,
                    reason=reason,
                    claimed_by_user_id=owner if claimed else None,
                    claimed_at=started + timedelta(minutes=20) if claimed else None,
                    created_at=started + timedelta(minutes=5),
                ))
                made["escalations"] += 1

            # What the call cost, for the calls. Shaped like a real one: the
            # input grows every turn because a realtime call re-bills the whole
            # conversation, and most of it is cached.
            if channel == "voice":
                history = 0
                for turn in range(len(lines) // 2 + 1):
                    history += 270
                    cached = 0 if turn == 0 else int((3400 + history) * 0.85)
                    db.add(CallUsage(
                        conversation_id=convo.id, response_id=f"demo-{n}-{visit}-{turn}",
                        model="gpt-realtime",
                        input_tokens=3400 + history,
                        input_audio_tokens=max(history - int(cached * 0.4), 0),
                        input_text_tokens=max(3400 - int(cached * 0.6), 0),
                        cached_tokens=cached,
                        cached_audio_tokens=int(cached * 0.4),
                        output_tokens=200, output_audio_tokens=180, output_text_tokens=20,
                        created_at=started + timedelta(seconds=30 * turn),
                    ))
                    made["call_usage"] += 1

        # What they told us, taken from what they actually said. The budget is
        # derived from the car they were shown rather than rolled separately,
        # so a buyer looking at a $34,000 SUV is not on file wanting to spend
        # twelve.
        ceiling = ((car.price or 20000) + 2500) // 1000
        fields = [
            (need.field[0], need.field[1], "typed"),
            ("budget", f"around ${ceiling}k", "inferred"),
            ("timeline", rng.choice(["this week", "this month", "just looking"]), "inferred"),
        ]
        seen_keys = set()
        for key, value, provenance in fields:
            if key in seen_keys:
                continue
            seen_keys.add(key)
            db.add(CapturedField(
                lead_id=lead.id, key=key, value=value, provenance=provenance,
                updated_at=last_stamp,
            ))
            made["captured_fields"] += 1

        # Appointments: mostly history, and any future one at least eight days
        # out. `make smoke` books the next open slot and book_appointment
        # refuses a clash, so filling this week would break the gate.
        appointment = None
        if booked_convo is not None:
            future = n % 6 == 0
            if future:
                starts = _next_open_slot(
                    hours, now, 8 + rng.randint(0, 12), rng.choice([10, 11, 14, 15])
                )
                status = rng.choice(["booked", "confirmed"])
            else:
                starts = _next_open_slot(
                    hours, now - timedelta(days=rng.randint(3, 50)), 0,
                    rng.choice([10, 11, 14, 15]),
                )
                status = rng.choice(["completed", "cancelled", "no_show", "confirmed"])
            appointment = Appointment(
                lead_id=lead.id,
                # The car they were actually shown, and the rep who actually
                # owns them -- not two more independent rolls.
                vehicle_id=car.id,
                assigned_user_id=owner,
                starts_at=starts, status=status,
                booked_by=rng.choice(["liner", "liner", "rep"]),
                conversation_id=booked_convo.id,
                created_at=booked_convo.started_at,
            )
            db.add(appointment)
            db.flush()
            made["appointments"] += 1
            # Only a visit that is still standing leaves the thread at
            # `booked`. The conversations list reads that stage for its badge,
            # and the lead beside it derives the same thing from appointment
            # rows -- counting only booked and confirmed -- so a thread at
            # `booked` against a cancelled, completed or no-show appointment is
            # one buyer with two answers. That is the disagreement the cancel
            # path was fixed for, and demo rows must not put it back by
            # writing a state the product itself cannot produce.
            if status not in {"booked", "confirmed"}:
                booked_convo.stage = "contact_capture"

        if lead.email and n % 2:
            sent = last_stamp + timedelta(hours=1)
            when = appointment.starts_at if appointment is not None else None
            if when is not None and appointment.status in {"booked", "confirmed"}:
                kind = "reminder"
                subject = f"Your visit to see the {car.year} {car.make} {car.model}"
                body = (
                    f"You're booked in for {when:%A %-d %B at %-I:%M %p} to see the "
                    f"{car.year} {car.make} {car.model}. Reply here if anything changes."
                )
            else:
                kind = "followup"
                subject = f"That {car.make} {car.model} you asked about"
                body = (
                    f"You were after {need.want}. The {car.year} {car.make} {car.model} "
                    f"is still on the lot at ${car.price or 0:,}. Want to come and see it?"
                )
            db.add(Outreach(
                lead_id=lead.id, channel="email", direction="out", kind=kind,
                to_address=lead.email, subject=subject, body=body,
                provider="outbox", status="sent", sent_at=sent, created_at=sent,
            ))
            made["outreach"] += 1
            # Some of them wrote back. Inbound and outbound in one table is
            # what makes the mailbox a union rather than two lists.
            if n % 4 == 1:
                db.add(Outreach(
                    lead_id=lead.id, channel="email", direction="in", kind="reply",
                    to_address=lead.email, subject=f"Re: {subject}",
                    body=rng.choice([
                        "Yes please, Saturday works.",
                        f"Is the {car.model} still there?",
                        "Thanks -- I'll think about it.",
                    ]),
                    provider="inbound", status="sent",
                    sent_at=sent + timedelta(hours=3), created_at=sent + timedelta(hours=3),
                ))
                made["outreach"] += 1

        # Mail nobody could place -- a stranger writing to sales@. It has no
        # lead and no buyer page, and it is the reason /app/email is a union.
        if n % 6 == 0:
            db.add(InboundEmail(
                outcome="unresolved",
                message_id=f"<demo-{n}@example.invalid>",
                from_address=f"stranger{n}@example.invalid",
                to_address="sales@example.invalid",
                subject="Do you have anything cheaper?",
                body="Saw the listing online. What else is on the lot?",
                detail="Demo row: nobody by this address exists.", created_at=first_seen,
            ))
            made["inbound_emails"] += 1

        if last_convo is None:  # pragma: no cover - visits is always >= 1
            continue

    db.commit()
    return made


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    db = SessionLocal()
    try:
        made = build(db, count)
    finally:
        db.close()

    print(f"\nAdded {count} demo buyers on top of the fixture:\n")
    for table, n in sorted(made.items()):
        print(f"  {table:20} +{n}")
    print(
        "\nEvery address is @example.invalid and every phone is 555-01xx, so none of"
        "\nthis can be contacted. Appointments are in the past or 8+ days out, so"
        "\n`make smoke` still has slots to book. `make reset-db` clears it all."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
