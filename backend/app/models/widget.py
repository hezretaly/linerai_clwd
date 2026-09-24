"""What the website chat knows about the dealer's own site.

Two tables, both new rather than columns on something that exists, for the
reason every other late addition here is a table: `create_all` adds a table to
a database that already exists and never a column.

`conversation_pages` is where a buyer was on the dealer's website while they
talked to Liner -- the page, its title, and the car on it when the page named
one. It is the only thing that lets "is this one still available?" mean the car
the buyer is looking at, rather than a question about nothing.

`widget_installs` is where the loader has actually been seen running. A tag
pasted into a website template is invisible from here until it reports, and a
tag that was never pasted, or was pasted onto a staging host nobody listed,
looks exactly like a quiet week -- the failure `inbound_emails` exists to make
visible for mail, arriving through the website instead.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import created, pk


class ConversationPage(Base):
    """One page of the dealer's site a buyer had open while chatting.

    Written when the chat opens and again each time the buyer moves to another
    page with it open; a repeat of the page already on record is not written
    twice. **Everything here came from the buyer's browser**, so none of it is
    trusted: the URL must be on one of the dealership's own origins or it is
    not kept, the title is a label and never an instruction, and a VIN only
    becomes a car by being found through `tools.offerable` -- a sold or
    do-not-discuss car on an old tab is recorded as a VIN and nothing more.
    """

    __tablename__ = "conversation_pages"

    id: Mapped[str] = pk()
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    url: Mapped[str] = mapped_column(String(1000), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    #: As the page stated it, uppercased and checked for shape. Kept even when
    #: no offerable car has it: "the buyer was looking at a car that has since
    #: sold" is worth a rep knowing.
    vin: Mapped[str] = mapped_column(String(17), default="")
    vehicle_id: Mapped[str | None] = mapped_column(
        ForeignKey("vehicles.id"), nullable=True
    )
    seen_at: Mapped[datetime] = created()


class WidgetInstall(Base):
    """One website the loader has been seen running on.

    One row per origin, updated in place, so the table is bounded by the
    number of origins a dealership has listed -- a report from anywhere else
    is refused before it reaches here, which is also what stops a stranger
    growing it. `reports` counts page loads that reported, which is a rough
    measure of traffic rather than a figure anybody should quote.
    """

    __tablename__ = "widget_installs"

    id: Mapped[str] = pk()
    origin: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    first_seen_at: Mapped[datetime] = created()
    last_seen_at: Mapped[datetime] = created()
    last_page: Mapped[str] = mapped_column(String(1000), default="")
    loader_version: Mapped[str] = mapped_column(String(20), default="")
    #: Other chat products the loader found on the page, by name. A list of
    #: short strings from the loader's own fingerprint table.
    other_widgets_json: Mapped[str] = mapped_column(Text, default="[]")
    #: The tag was on the page twice -- in the site template *and* through
    #: Google Tag Manager is the usual way.
    duplicate_tag: Mapped[bool] = mapped_column(Boolean, default=False)
    #: A Google Tag Manager `dataLayer` was present, so lead events had
    #: somewhere to go.
    gtm_present: Mapped[bool] = mapped_column(Boolean, default=False)
    reports: Mapped[int] = mapped_column(Integer, default=0)
