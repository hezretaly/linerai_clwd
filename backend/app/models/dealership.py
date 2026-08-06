from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
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
    published_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
