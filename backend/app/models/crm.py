from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow
from app.models.base import created, new_id

# Stages drive the rails and the stub agent (§7.4). Recomputed at the end of
# every agent turn.
STAGES = [
    "opening",
    "browsing",
    "vehicle_focus",
    "objection",
    "qualifying",
    "slot_offered",
    "contact_capture",
    "booked",
    "escalated",
]

PROVENANCE = ["typed", "listing", "caller_id", "inferred"]


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    phone: Mapped[str] = mapped_column(String(40), default="")
    source: Mapped[str] = mapped_column(String(20), default="chat")  # chat|phone|website
    assigned_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    email_consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created()

    @property
    def contact_risk(self) -> bool:
        """No email on file means the product cannot reach them (§18.5)."""
        return not self.email


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(10), default="chat")  # chat | voice
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|handoff|closed
    # Separate from status: a rep can hold a thread that is still 'active'.
    agent_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    stage: Mapped[str] = mapped_column(String(30), default="opening")
    focus_vehicle_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # VINs from the most recent search, in the order the buyer saw them, so
    # "tell me about the first one" resolves to the car actually shown first.
    last_results_json: Mapped[str] = mapped_column(Text, default="[]")
    # The slots the buyer was offered, and the one they picked. Without this a
    # booking can land on a different time than the one they agreed to.
    offered_slots_json: Mapped[str] = mapped_column(Text, default="[]")
    chosen_slot: Mapped[str] = mapped_column(String(40), default="")
    started_at: Mapped[datetime] = created()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # buyer | assistant | rep
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls_json: Mapped[str] = mapped_column(Text, default="[]")
    via_rail_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = created()


class CapturedField(Base):
    """Key/value keeps the set open; provenance is what makes the UI honest.

    The executor rejects provenance='typed' for anything the buyer did not
    actually say -- the model must not be able to launder a guess (§18.2).
    """

    __tablename__ = "captured_fields"
    __table_args__ = (UniqueConstraint("lead_id", "key", name="uq_captured_lead_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    value: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(20))  # typed|listing|caller_id|inferred
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), index=True)
    vehicle_id: Mapped[str | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    assigned_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_min: Mapped[int] = mapped_column(Integer, default=30)
    # booked -> confirmed -> (cancelled | no_show)
    status: Mapped[str] = mapped_column(String(20), default="booked")
    booked_by: Mapped[str] = mapped_column(String(10), default="liner")  # liner | rep
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    created_at: Mapped[datetime] = created()


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    handoff_rule_id: Mapped[str | None] = mapped_column(ForeignKey("handoff_rules.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    claimed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    created_at: Mapped[datetime] = created()


class Outreach(Base):
    """Provider-neutral column names so an EmailSender swap is a code change,
    not a migration. status goes queued -> sent on API ack; there is no
    delivery webhook (§0)."""

    __tablename__ = "outreach"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    appointment_id: Mapped[str | None] = mapped_column(ForeignKey("appointments.id"), index=True)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    sent_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")  # email | phone_logged
    to_address: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(30), default="")
    provider_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_thread_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|sent|bounced|failed
    error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created()


class Event(Base):
    """Append-only. Doubles as the audit log and the WebSocket reconnect buffer."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(60), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = created()
