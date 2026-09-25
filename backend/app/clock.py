"""The dealership's own clock -- one definition of "now" and "today" in its
timezone, for everything that is not a UTC instant.

Two frames exist on this system and they were being mixed. Appointment
``starts_at`` is naive **wall-clock** time in the dealership's own zone --
``check_availability`` builds it straight out of ``hours_json`` in that frame,
and the CLAUDE.md rule is "naive timestamps are dealership-local". Everything
else stored here (``Conversation.started_at``, ``Lead.created_at``, ...) is a
naive **UTC instant** -- ``db.utcnow()``.

Before this module, callers reached for ``db.utcnow()`` and truncated or
compared it directly against wall-clock columns, which is correct only when
the dealership happens to be on UTC. From about 19:00 to midnight in
America/Chicago (5-6 hours behind UTC), "today" by the UTC clock is already
tomorrow's date, so a day boundary, an hour bucket or "today's appointments"
silently shifted by the UTC offset. This module is the one place that
conversion happens.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.db import utcnow
from app.models import Dealership

_UTC = ZoneInfo("UTC")


def tz(dealership: Dealership) -> ZoneInfo:
    return ZoneInfo(dealership.timezone or "America/Chicago")


def local_now(dealership: Dealership) -> datetime:
    """The dealership's own wall-clock instant, aware, in its own zone."""
    return datetime.now(_UTC).astimezone(tz(dealership))


def wall_now(dealership: Dealership) -> datetime:
    """The dealership's own wall-clock **now**, naive -- the frame
    ``Appointment.starts_at`` is stored in. Use this, never ``db.utcnow()``,
    anywhere a naive wall-clock column is compared against "now"."""
    return local_now(dealership).replace(tzinfo=None)


def today(dealership: Dealership) -> date:
    """The calendar date it is right now, at the showroom."""
    return local_now(dealership).date()


def day_start_utc(dealership: Dealership, d: date) -> datetime:
    """Local midnight of ``d``, converted to a naive UTC instant -- the frame
    every UTC-stamped column (``started_at``, ``created_at``, ...) is stored
    in. DST-correct: built from an aware local midnight and converted, never
    by adding a fixed offset."""
    local_midnight = datetime.combine(d, datetime.min.time(), tzinfo=tz(dealership))
    return local_midnight.astimezone(_UTC).replace(tzinfo=None)


def local_hour(dealership: Dealership, ts_utc_naive: datetime) -> int:
    """The dealership-local hour (0-23) a naive UTC instant falls in."""
    return ts_utc_naive.replace(tzinfo=_UTC).astimezone(tz(dealership)).hour


def local_date(dealership: Dealership, ts_utc_naive: datetime) -> date:
    """The dealership-local calendar date a naive UTC instant falls in."""
    return ts_utc_naive.replace(tzinfo=_UTC).astimezone(tz(dealership)).date()


def day_bounds_utc(dealership: Dealership, d: date) -> tuple[datetime, datetime]:
    """[start, end) of local day ``d``, both naive UTC instants."""
    start = day_start_utc(dealership, d)
    return start, day_start_utc(dealership, d + timedelta(days=1))
