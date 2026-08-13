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

WANTS = [
    "third row for the school run", "something under twenty grand",
    "an SUV with all-wheel drive", "low mileage, doesn't matter what badge",
    "a hybrid -- the commute is killing me", "first car for my daughter",
    "trading in a 2015 Civic", "needs to tow a small trailer",
    "wants leather and a sunroof", "replacing a car that got written off",
]

ASKS = [
    "What's the mileage on that one?",
    "Is the price negotiable at all?",
    "Can I see it this weekend?",
    "Does it have a clean title?",
    "How much would you give me for my trade?",
    "What's the doc fee here?",
    "Is that the out-the-door price?",
    "Do you offer financing in house?",
]

REPLIES = [
    "That one's listed at the price on the page -- want me to pull the details?",
    "I can check what's open this week if you'd like to come and see it.",
    "Let me look that up rather than guess.",
    "A colleague handles trade valuations -- shall I have them call you?",
    "It's been on the lot two weeks, so it's still available.",
]

ESCALATION_REASONS = [
    "Asked for an out-the-door price",
    "Wants to talk to a manager",
    "Credit question -- past repossession",
    "Ready to sign today",
    "Asked about a car we do not have",
]


def demo_email(name: str, n: int) -> str:
    """`.invalid` is reserved by RFC 2606 and can never resolve. That is the
    point: a demo lead cannot be emailed even by a fully configured sender with
    the outbound limit lifted."""
    handle = name.lower().replace(" ", ".")
    return f"{handle}.{n}@example.invalid"


def build(db, count: int) -> dict[str, int]:
    random.seed(20260813 + count)
    now = utcnow()
    dealership = db.query(Dealership).first()
    if dealership is None:
        raise SystemExit("No dealership. Run `make seed` first.")
    hours = json.loads(dealership.hours_json)
    reps = db.query(User).filter_by(role="rep", active=True).all()
    vehicles = db.query(Vehicle).filter_by(status="available").all()
    if not vehicles:
        raise SystemExit("No vehicles. Run `make seed` first.")

    made = dict.fromkeys(
        ("leads", "conversations", "messages", "captured_fields", "appointments",
         "outreach", "escalations", "vehicle_mentions", "inbound_emails",
         "call_usage"), 0,
    )

    for n in range(count):
        name = f"{random.choice(FIRST)} {random.choice(LAST)}"
        # Spread over eight weeks so the charts have a shape and the
        # conversations list has something to sort.
        ago = timedelta(days=random.randint(0, 56), hours=random.randint(0, 12))
        started = now - ago

        lead = Lead(
            name=name,
            # Every tenth buyer has no email on file, because that is a real
            # state the dashboard flags and an all-reachable demo hides it.
            email="" if n % 10 == 9 else demo_email(name, n),
            phone=f"+1555010{n % 100:02d}",
            source=random.choice(["chat", "chat", "phone", "website", "adf"]),
            assigned_user_id=random.choice(reps).id if reps and n % 3 else None,
            created_at=started,
        )
        db.add(lead)
        db.flush()
        made["leads"] += 1

        # Roughly a third of buyers used the phone, which is what makes the
        # channel strip on the buyer page worth looking at.
        channel = "voice" if n % 3 == 0 else "chat"
        closed = n % 7 != 0
        convo = Conversation(
            lead_id=lead.id,
            channel=channel,
            status="closed" if closed else "active",
            stage=random.choice(["qualifying", "recommending", "booked", "opening"]),
            started_at=started,
            ended_at=started + timedelta(minutes=random.randint(3, 14)) if closed else None,
            summary=f"Wanted {random.choice(WANTS)}." if closed else "",
        )
        db.add(convo)
        db.flush()
        made["conversations"] += 1

        # A short thread. Enough that a transcript reads like one, not so much
        # that fifty of them make the database unwieldy.
        stamp = started
        for turn in range(random.randint(3, 8)):
            stamp += timedelta(seconds=random.randint(20, 90))
            buyer = turn % 2 == 0
            db.add(Message(
                conversation_id=convo.id,
                role="buyer" if buyer else "assistant",
                content=(
                    random.choice(ASKS) if buyer
                    else random.choice(REPLIES)
                ),
                created_at=stamp,
            ))
            made["messages"] += 1

        for key, value, provenance in (
            ("budget", f"${random.randrange(12, 45)},000", "typed"),
            ("timeline", random.choice(["this week", "this month", "just looking"]), "inferred"),
            ("trade_in", random.choice(["yes", "no"]), "typed"),
        ):
            db.add(CapturedField(
                lead_id=lead.id, key=key, value=value, provenance=provenance,
                updated_at=stamp,
            ))
            made["captured_fields"] += 1

        car = random.choice(vehicles)
        db.add(VehicleMention(
            conversation_id=convo.id, vehicle_id=car.id,
            quoted_price=car.price, created_at=stamp,
        ))
        made["vehicle_mentions"] += 1

        # Appointments: mostly history, and any future one at least eight days
        # out. `make smoke` books the next open slot and book_appointment
        # refuses a clash, so filling this week would break the gate.
        if n % 2 == 0:
            future = n % 6 == 0
            if future:
                starts = _next_open_slot(
                    hours, now, 8 + random.randint(0, 12), random.choice([10, 11, 14, 15])
                )
                status = random.choice(["booked", "confirmed"])
            else:
                starts = _next_open_slot(
                    hours, now - timedelta(days=random.randint(3, 50)), 0,
                    random.choice([10, 11, 14, 15]),
                )
                status = random.choice(["completed", "cancelled", "no_show", "confirmed"])
            db.add(Appointment(
                lead_id=lead.id, vehicle_id=car.id,
                assigned_user_id=random.choice(reps).id if reps else None,
                starts_at=starts, status=status,
                booked_by=random.choice(["liner", "liner", "rep"]),
                conversation_id=convo.id, created_at=started,
            ))
            made["appointments"] += 1

        if lead.email and n % 2:
            sent = started + timedelta(hours=1)
            db.add(Outreach(
                lead_id=lead.id, channel="email", direction="out",
                kind=random.choice(["followup", "reminder", "credit_application"]),
                to_address=lead.email,
                subject=random.choice([
                    "Following up on your visit", "That third row you asked about",
                    "Your appointment at Riverside Auto",
                ]),
                body="Thanks for getting in touch -- happy to answer anything else.",
                provider="outbox", status="sent", sent_at=sent, created_at=sent,
            ))
            made["outreach"] += 1
            # Some of them wrote back. Inbound and outbound in one table is
            # what makes the mailbox a union rather than two lists.
            if n % 4 == 1:
                db.add(Outreach(
                    lead_id=lead.id, channel="email", direction="in", kind="reply",
                    to_address=lead.email, subject="Re: Following up on your visit",
                    body=random.choice([
                        "Yes please, Saturday works.",
                        "Is that still available?",
                        "Thanks -- I'll think about it.",
                    ]),
                    provider="inbound", status="sent",
                    sent_at=sent + timedelta(hours=3), created_at=sent + timedelta(hours=3),
                ))
                made["outreach"] += 1

        # Open handoffs on a minority, because a queue where everything is
        # flagged tells a rep nothing.
        if n % 5 == 0:
            claimed = n % 15 == 0
            db.add(Escalation(
                conversation_id=convo.id,
                reason=random.choice(ESCALATION_REASONS),
                claimed_by_user_id=random.choice(reps).id if claimed and reps else None,
                claimed_at=started + timedelta(minutes=20) if claimed else None,
                created_at=started + timedelta(minutes=5),
            ))
            made["escalations"] += 1

        # Mail nobody could place -- a stranger writing to sales@. It has no
        # lead and no buyer page, and it is the reason /app/email is a union.
        if n % 6 == 0:
            db.add(InboundEmail(
                outcome=random.choice(["unresolved", "accepted", "duplicate"]),
                message_id=f"<demo-{n}@example.invalid>",
                from_address=f"stranger{n}@example.invalid",
                to_address="sales@example.invalid",
                subject="Do you have anything cheaper?",
                body="Saw the listing online. What else is on the lot?",
                detail="Demo row.", created_at=started,
            ))
            made["inbound_emails"] += 1

        # What the call cost, for the calls. Shaped like a real one: the input
        # grows every turn because a realtime call re-bills the whole
        # conversation, and most of it is cached.
        if channel == "voice":
            history = 0
            for turn in range(random.randint(3, 9)):
                history += 270
                cached = 0 if turn == 0 else int((3400 + history) * 0.85)
                db.add(CallUsage(
                    conversation_id=convo.id, response_id=f"demo-{n}-{turn}",
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
