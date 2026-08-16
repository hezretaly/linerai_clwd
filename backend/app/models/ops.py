"""Liner's own tables. Everything here is ours; nothing here is a dealership's.

The `ops_` prefix is not decoration. Our accounts used to live in `users`
alongside the dealership's staff, separated only by a role string -- and that
meant every unfiltered `query(User)` in the codebase was a place we could
surface inside somebody else's showroom. They did: on the team roster, in the
assignment pickers, and behind the public demo door. Each was a real bug, each
was fixed with a predicate, and the next unfiltered query would have brought
them all back.

A separate table cannot be queried by accident. That is the whole argument:
the separation stops being something five call sites have to remember and
becomes something the schema enforces.

Still one database, deliberately. Two would mean two connections, two backups,
two `create_all`s and no way to read both sides in one request -- and the
`events` table the dealer socket replays from lives on the other side of that
line. What the split buys is the thing that actually went wrong; a second
database buys isolation against a threat that does not exist while we are the
only operator. If it ever does, these are the tables that move, and they are
already the only ones that would.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import created, new_id


class OpsUser(Base):
    """Us. Not a `User` -- a `User` works at the dealership.

    The two tables have almost the same columns and that is not a reason to
    merge them: `daily_cap` and `notify_channel` are about taking appointments
    on a showroom floor, which nobody here does. What they share is a name, an
    address and a password, which is what any account is.
    """

    __tablename__ = "ops_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    #: The `.env` key this account's password is read from at seed time, so
    #: two people can have two passwords without a variable per person being
    #: invented at every call site. Empty falls back to OWNER_PASSWORD.
    password_env: Mapped[str] = mapped_column(String(40), default="")
    avatar_initials: Mapped[str] = mapped_column(String(4), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()

    #: Kept so `current_user`-shaped code can ask without a branch. There is
    #: exactly one ops role and it is not a dealership's; anything checking
    #: `role == "manager"` must never match one of these by accident.
    @property
    def role(self) -> str:
        return "owner"


class DemoRequest(Base):
    """Somebody asking Liner AI for a demo, or for help.

    Not a `Lead`. A lead is a person buying a car from the dealership; this is
    a dealership buying Liner. They share almost nothing -- no inventory, no
    conversation, no appointment against a vehicle -- and folding them into one
    table would put strangers into the buyer list a rep works from, which is
    the one list on this dashboard that has to mean exactly one thing.

    One table for both kinds because the shape is identical: somebody, a way to
    reach them, and what they said. A support request simply has no slot.
    """

    __tablename__ = "ops_demo_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    #: demo | support
    kind: Mapped[str] = mapped_column(String(12), default="demo", index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    dealership: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    phone: Mapped[str] = mapped_column(String(40), default="")
    dealership_url: Mapped[str] = mapped_column(String(500), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    #: The slot they picked, in the same naive dealership-local frame every
    #: other timestamp here uses. Null on a support request.
    slot_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    #: When they ticked the box, not whether. A boolean records that a checkbox
    #: was checked; a timestamp records consent being given at a moment, which
    #: is the thing anyone would ever have to show.
    consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: The exact words they agreed to, kept with the row. The wording will be
    #: edited on the page one day, and a consent record that points at whatever
    #: the page says *now* is not a record of what they agreed to *then*.
    consent_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = created()


#: Every table on our side of the line, by name. `make reset-db` reads this to
#: leave them alone: rebuilding the dealership's fixture must not throw away
#: the demos people booked with us, which are real bookings with real people
#: on the other end.
OPS_TABLES = (OpsUser.__tablename__, DemoRequest.__tablename__)
