from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow
from app.models.base import created, new_id


class Dealership(Base):
    """Exactly one row, seeded from config/dealership.yaml."""

    __tablename__ = "dealership"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago")
    hours_json: Mapped[str] = mapped_column(Text, default="{}")
    address: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    website_url: Mapped[str] = mapped_column(String(255), default="")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="rep")  # manager | rep
    avatar_initials: Mapped[str] = mapped_column(String(4), default="")
    daily_cap: Mapped[int] = mapped_column(Integer, default=8)
    notify_channel: Mapped[str] = mapped_column(String(20), default="email")  # email | dashboard
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Temporarily off the floor -- lunch, a day off -- never what they already
    # own. That is `active` (deactivate, hand everything back); `out` changes
    # nothing about who owns what, only whether they take new work.
    out: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created()


class AssistantSettings(Base):
    """Draft vs live matters: an edit must not reach a buyer until published."""

    __tablename__ = "assistant_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | live
    tone: Mapped[str] = mapped_column(String(30), default="warm")
    push_level: Mapped[str] = mapped_column(String(30), default="balanced")
    price_mode: Mapped[str] = mapped_column(String(30), default="listed_only")
    discount_pct: Mapped[int] = mapped_column(Integer, default=0)
    financing_mode: Mapped[str] = mapped_column(String(30), default="refer_to_rep")
    after_hours_mode: Mapped[str] = mapped_column(String(30), default="full_service")
    greeting: Mapped[str] = mapped_column(Text, default="")
    booking_slot_length: Mapped[int] = mapped_column(Integer, default=30)
    # The dealer's own finance application. Empty by default and empty is
    # meaningful: with no link there is nothing to send, so the action says
    # so rather than mailing a buyer an invitation to apply nowhere.
    credit_application_url: Mapped[str] = mapped_column(String(500), default="")
    published_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AssistantPrompt(Base):
    """A dealership's own prompt: the one text on the Instructions tab.

    **`brief` is the whole of it** -- how Liner talks to their buyers, in the
    manager's words. Everything else the model is told (the rules, the facts,
    each channel's instructions, the writing assistant) is product code in
    `agent/prompts.py` and follows it. `rules` is no longer read: it was the
    second box when there were two, and it is written empty.

    **Per settings version, so it is drafted and published with everything
    else.** An edit lands on the draft's row and reaches a buyer only when a
    manager publishes -- the rule this page is built on, and the one that
    matters most for the text the model is actually told. A table rather than
    a column on `assistant_settings` because `create_all` added a table to a
    database that already existed and never a column.

    Empty means Liner's own (`prompts.default_prompt`), so a dealership that
    has never touched it follows every improvement to the default, and Reset
    is emptying the box rather than pasting a copy that then goes stale.
    """

    __tablename__ = "assistant_prompts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    settings_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_settings.id"), unique=True, index=True
    )
    brief: Mapped[str] = mapped_column(Text, default="")
    rules: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AssistantPart(Base):
    """**No longer read or written.** A dealership's own wording for one
    channel -- the website chat, the phone line, the email replies or the
    writing assistant -- from when the Instructions tab had a box for each.

    There is one assistant with one prompt now (`AssistantPrompt.brief`), and
    what each channel adds is product code: a call needing to be told it has
    no screen is not a matter of a dealership's taste, and a manager who is
    not technical was being asked to edit tool mechanics to change how Liner
    sounds. Kept, with its migration, because dropping a table is a revision
    with no rows to justify it -- none was ever saved on a live server.
    """

    __tablename__ = "assistant_parts"
    # **One row per part per version.** Nothing said so, so a second row for
    # the same part was possible -- and then which wording an assistant ran
    # under was whichever row the database returned last: insertion order on
    # SQLite, anything on Postgres. The settings API's own `one_or_none` would
    # have failed on the pair outright. Migration 0002 keeps the newest of any
    # pair already written, then adds this.
    __table_args__ = (UniqueConstraint("settings_id", "part"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    settings_id: Mapped[str] = mapped_column(ForeignKey("assistant_settings.id"), index=True)
    part: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class HandoffRule(Base):
    """The five escalation triggers. The only home for escalation config (§0)."""

    __tablename__ = "handoff_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(60), unique=True)
    label: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    threshold_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    threshold_unit: Mapped[str] = mapped_column(String(30), default="")
    route_target: Mapped[str] = mapped_column(String(60), default="any_available")
    notify: Mapped[str] = mapped_column(String(40), default="email_dashboard")
    #: Unused. "Fired N times" is derived from escalation rows now
    #: (`app.escalations.fired_counts`) rather than this hand-incremented
    #: column, which only one writer ever moved while several others added
    #: escalation rows it never saw. Kept rather than dropped: there is no
    #: migration in this change, and a column `create_all` already built is
    #: not worth one on its own.
    fired_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class KnowledgeEntry(Base):
    """What the listings don't cover. Injected into the prompt; cuts hallucination."""

    __tablename__ = "knowledge_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    topic: Mapped[str] = mapped_column(String(120))
    answer: Mapped[str] = mapped_column(Text)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Rail(Base):
    """Clickable buyer prompts (§7.4). Input sugar -- tapping one sends its
    message_text as an ordinary buyer message through the same code path."""

    __tablename__ = "rails"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(20))  # opener | followup | knowledge
    stage: Mapped[str] = mapped_column(String(30), default="")
    label: Mapped[str] = mapped_column(String(120))
    message_text: Mapped[str] = mapped_column(Text)
    requires_vehicle: Mapped[bool] = mapped_column(Boolean, default=False)
    knowledge_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    advances_to: Mapped[str] = mapped_column(String(30), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: `{"do": "under_price", "args": {"max_price": 20000}}`, or empty.
    #:
    #: A chip's meaning is fixed by whoever put it on screen, so the ones that
    #: are just a search answer themselves -- the tool runs, the sentence is
    #: built from its result, and no model turn happens. See
    #: `agent/rail_actions.py`. Empty means the model reads the chip's text
    #: like any other buyer message, which is what every chip did before.
    #:
    #: Deliberately not four columns. `do` and `args` vary per action and a
    #: column per argument would be a schema change every time somebody adds a
    #: chip, in a codebase with no migrations.
    action_json: Mapped[str] = mapped_column(Text, default="")
