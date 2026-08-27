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

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
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


class OpsMessage(Base):
    """Mail *we* wrote: a draft being written, or one that has gone out.

    Sending used to leave no row at all -- `sender.send()` was called and the
    result returned to the browser -- so there was no Sent box to have, and no
    way to answer "did I already write to them?" a day later. Outbound is the
    half of a mailbox you reread most.

    A draft is the same row before it goes. One state column rather than two
    tables, because a draft becoming a sent message is the ordinary path and
    copying it across tables is how the two versions drift.

    **This is ours and only ours.** A dealership's outreach is an `Outreach`
    row against a lead, carrying a `reply+<token>@` return path that routes an
    answer into that buyer's timeline. Nothing here has a lead, and the
    `Reply-To` is a person's own address.
    """

    __tablename__ = "ops_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    #: Who wrote it. Drafts are private to their author; Sent is shared,
    #: because "has anyone answered these people yet" is the question two
    #: people sharing an inbox actually ask.
    author_id: Mapped[str] = mapped_column(String(36), index=True)
    to_address: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    #: draft | sent | failed. `failed` is kept rather than discarded: a send
    #: the provider refused is the one a person most needs to find again, and
    #: dropping it loses what they typed.
    state: Mapped[str] = mapped_column(String(12), default="draft", index=True)
    #: The envelope as it actually went, resolved by `outreach_send`. Stored
    #: rather than recomputed, because SENDING_DOMAIN can change and a Sent
    #: item must keep saying what was really on it.
    from_address: Mapped[str] = mapped_column(String(320), default="")
    reply_to: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(40), default="")
    provider_message_id: Mapped[str] = mapped_column(String(255), default="")
    #: The provider's own words on the outcome, verbatim. With the outbox
    #: sender this is what says nothing left the building.
    detail: Mapped[str] = mapped_column(Text, default="")
    #: What it answers, when it answers something: 'form' or 'email' plus that
    #: row's id. Free text rather than a foreign key, because the two sources
    #: live in different tables and a nullable pair of FKs buys nothing.
    reply_to_kind: Mapped[str] = mapped_column(String(12), default="")
    reply_to_id: Mapped[str] = mapped_column(String(36), default="")
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    #: Trash is a timestamp, never a delete. A message somebody wrote is the
    #: last thing to destroy on their behalf, and Trash that cannot be undone
    #: is a delete button wearing a friendlier word.
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class OpsMailState(Base):
    """Read and trash marks for mail we did *not* write.

    A separate table because the two sources it covers are separate tables and
    neither can gain a column: there is no Alembic here by design, so a new
    column simply does not appear on a database that already exists, while a
    new table does. That constraint is why an unresolved delivery was
    hardcoded `"unread": False` -- every one of them arrived looking already
    read, which is the opposite of what an inbox is for.

    Keyed on (kind, ref_id) rather than per person. Two of us share this
    mailbox, and "I have read it" from either is the answer the other needs --
    the same reasoning that made `ops_demo_requests.status` a state on the row
    instead of a per-user receipt. A per-person flag would have the unread
    count arguing with itself across two laptops.

    **Forms are not in here for read state.** `ops_demo_requests.status` is
    already that fact and the notification bell reads it; a second copy is how
    the bell and the mailbox start disagreeing about the same message. This
    table carries read state for `inbound_emails`, and trash for both.
    """

    __tablename__ = "ops_mail_state"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    #: form | email -- which table `ref_id` points into.
    kind: Mapped[str] = mapped_column(String(12), index=True)
    ref_id: Mapped[str] = mapped_column(String(36), index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = created()

    __table_args__ = (
        UniqueConstraint("kind", "ref_id", name="uq_ops_mail_state_ref"),
    )


#: Every table on our side of the line, by name. `make reset-db` reads this to
#: leave them alone: rebuilding the dealership's fixture must not throw away
#: the demos people booked with us, which are real bookings with real people
#: on the other end -- nor the mail we wrote them.
OPS_TABLES = (
    OpsUser.__tablename__,
    DemoRequest.__tablename__,
    OpsMessage.__tablename__,
    OpsMailState.__tablename__,
)
