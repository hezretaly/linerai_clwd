"""What counts as a live, upcoming, or unconfirmed appointment -- one
definition, read by the Overview KPI, the sidebar Calendar badge, each lead's
`appointment_set`/`unconfirmed_count`, and the Calendar page.

Before this module the same idea -- "does this buyer have an appointment
coming up" -- was answered four separate ways: `conversations.stage ==
'booked'` (written once by `book_appointment`, never walked back on a
cancel, a no-show or a visit that has simply passed); `Appointment.status ==
'booked'` with no time bound (the sidebar badge, counting a booked-and-never-
confirmed visit from a month ago as urgently as one tomorrow); `status in
('booked', 'confirmed')` with no time bound (a lead's `appointment_count`);
and the Calendar's own client-side `status not in (cancelled, no_show) and
starts_at + duration > now`, evaluated on the browser's clock against a
naive wall-clock timestamp.

``starts_at`` is dealership-local wall-clock time (`check_availability`
builds it from `hours_json` in that frame), so "now" here has to be the
dealership's own wall clock (`app.clock.wall_now`), never `db.utcnow()`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import clock
from app.api.deps import assignable_query
from app.models import Appointment, Dealership, User

#: A visit that is booked, but the dealer has not yet confirmed it.
UNCONFIRMED_STATUSES = ("booked",)

#: A visit that still occupies a slot -- the buyer has not been marked as a
#: no-show, and nobody cancelled it. This is what "an appointment is set"
#: means, and what clash checks continue to test alone.
STANDING_STATUSES = ("booked", "confirmed")

#: A booked visit that is not going to happen. The complement of
#: STANDING_STATUSES over every status this API can write.
OFF_STATUSES = ("cancelled", "no_show")


def is_upcoming(a: Appointment, now) -> bool:
    """True while the visit is still ahead of *now* (the dealership's own
    wall clock) and has not been cancelled or marked a no-show. A visit in
    progress -- start has passed but the slot has not ended -- still counts,
    which is the Calendar's own "still to come" rule."""
    return a.status in STANDING_STATUSES and a.starts_at + timedelta(minutes=a.duration_min) >= now


def upcoming(db: Session, dealership: Dealership) -> list[Appointment]:
    """Every standing appointment that has not finished yet, oldest first."""
    now = clock.wall_now(dealership)
    rows = (
        db.query(Appointment)
        .filter(
            Appointment.status.in_(STANDING_STATUSES),
            Appointment.starts_at >= now - timedelta(days=1),
        )
        .order_by(Appointment.starts_at.asc())
        .all()
    )
    return [a for a in rows if is_upcoming(a, now)]


def unconfirmed(db: Session, dealership: Dealership) -> list[Appointment]:
    """Upcoming visits the dealer has not yet confirmed -- the sidebar
    Calendar badge and the Overview "Unconfirmed appointments" row. Time-
    bounded, unlike the old `status == 'booked'` query: a booked visit whose
    time has already passed without anyone confirming it belongs in
    `unmarked`, not in a badge a rep reads as "needs a call today"."""
    return [a for a in upcoming(db, dealership) if a.status in UNCONFIRMED_STATUSES]


def unmarked(db: Session, dealership: Dealership) -> list[Appointment]:
    """Booked visits whose time has passed with nobody marking them
    confirmed, cancelled or a no-show -- a queue of its own, since nothing in
    this system clears such a row automatically."""
    now = clock.wall_now(dealership)
    # Duration varies per row, so it cannot be added inside the SQL filter --
    # `starts_at < now` is the loose bound and the exact end check runs here.
    rows = (
        db.query(Appointment)
        .filter(Appointment.status == "booked", Appointment.starts_at < now)
        .order_by(Appointment.starts_at.desc())
        .all()
    )
    return [a for a in rows if a.starts_at + timedelta(minutes=a.duration_min) < now]


def appointment_set(db: Session, dealership: Dealership, ids: list[str] | None = None) -> dict[str, bool]:
    """`lead_id -> True` for every lead with at least one upcoming standing
    appointment. This is "has an appointment set", replacing
    `conversations.stage == 'booked'`, which a cancel, an escalation or a
    visit simply passing never walked back."""
    rows = upcoming(db, dealership)
    out: dict[str, bool] = {}
    for a in rows:
        if ids is not None and a.lead_id not in ids:
            continue
        out[a.lead_id] = True
    return out


def capacity(db: Session, dealership: Dealership) -> int:
    """How many appointments one slot can hold: one per person who could
    actually run a visit. A showroom with three staff can show three buyers
    around at ten o'clock; a fourth booked into the same hour has nobody left
    to greet them. `dealership` is unused here but kept in the signature to
    match every other function in this module, all of which answer a
    question about one dealership's calendar."""
    return assignable_query(db).count()


def slot_taken(db: Session, starts_at: datetime, exclude_id: str | None = None) -> int:
    """How many standing appointments already sit at this exact time -- what
    a clash check compares against `capacity()` now that a slot is not one
    appointment but as many as the dealership has staff. `exclude_id` is for
    a reschedule, which must not count the appointment being moved against
    itself."""
    query = db.query(Appointment).filter(
        Appointment.starts_at == starts_at,
        Appointment.status.in_(STANDING_STATUSES),
    )
    if exclude_id is not None:
        query = query.filter(Appointment.id != exclude_id)
    return query.count()


def rep_conflict(
    db: Session,
    user_id: str,
    starts_at: datetime,
    duration_min: int,
    exclude_id: str | None = None,
) -> Appointment | None:
    """The rep's own standing appointment that truly overlaps *starts_at*, or
    None -- a slot can hold several buyers, but not the same person twice.

    Same idiom as `unmarked`: duration varies per row, so it cannot be added
    inside the SQL filter. The loose bound here is "starts before the new
    appointment ends"; the exact check -- an existing row's own end must fall
    after the new appointment's start -- runs in Python below.
    """
    end = starts_at + timedelta(minutes=duration_min)
    query = db.query(Appointment).filter(
        Appointment.assigned_user_id == user_id,
        Appointment.status.in_(STANDING_STATUSES),
        Appointment.starts_at < end,
    )
    if exclude_id is not None:
        query = query.filter(Appointment.id != exclude_id)
    for row in query.order_by(Appointment.starts_at.asc()).all():
        if row.starts_at + timedelta(minutes=row.duration_min) > starts_at:
            return row
    return None


def assign_open_appointments(db: Session, dealership: Dealership, lead_id: str, user_id: str) -> int:
    """Give a newly-owned buyer's open appointments to the person who now owns
    them -- the calendar's half of what `assign_lead` already does for
    escalations when somebody is assigned. Skips silently on a conflict or on
    an out/deactivated rep rather than raising: it stays unassigned for a
    person to notice on the calendar, since the caller's own assignment must
    not fail over a rep's unrelated appointment, or their break, somewhere
    else. Never raises.

    The eligibility check matters here as much as it does in
    `book_appointment` and the manual/auto branches of `POST
    /appointments/{id}/assign` -- without it, taking a buyer over from
    someone marked out could still leave that person hosting a visit
    `PATCH /appointments/{id}/assign` would itself have refused to give them.
    """
    if assignable_query(db).filter(User.id == user_id).first() is None:
        return 0
    assigned = 0
    for appt in upcoming(db, dealership):
        if appt.lead_id != lead_id or appt.assigned_user_id:
            continue
        if rep_conflict(db, user_id, appt.starts_at, appt.duration_min, exclude_id=appt.id):
            continue
        appt.assigned_user_id = user_id
        assigned += 1
    return assigned
