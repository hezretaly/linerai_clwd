from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import created, new_id


class Vehicle(Base):
    """The agent's source of truth. VIN is the natural key for dedupe."""

    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    vin: Mapped[str] = mapped_column(String(17), unique=True, index=True)
    year: Mapped[int] = mapped_column(Integer)
    make: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(60))
    trim: Mapped[str] = mapped_column(String(60), default="")
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_style: Mapped[str] = mapped_column(String(40), default="")
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title_status: Mapped[str] = mapped_column(String(40), default="clean")
    features_json: Mapped[str] = mapped_column(Text, default="[]")
    keywords: Mapped[str] = mapped_column(Text, default="")
    photo_url: Mapped[str] = mapped_column(String(255), default="")
    listing_url: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="available")  # available|sold|removed
    source: Mapped[str] = mapped_column(String(20), default="seed")  # scrape|manual|seed

    # Per-vehicle rules (§18.2). rule_discuss is enforced at the tool layer,
    # never at the prompt layer -- a do-not-discuss car never reaches the model.
    rule_discuss: Mapped[bool] = mapped_column(Boolean, default=True)
    rule_hold_price: Mapped[bool] = mapped_column(Boolean, default=False)
    rule_mention_warranty: Mapped[bool] = mapped_column(Boolean, default=False)
    rule_note: Mapped[str] = mapped_column(Text, default="")

    # Columns a human edited; the next ingest run must not overwrite these.
    manual_fields_json: Mapped[str] = mapped_column(Text, default="[]")

    first_seen_at: Mapped[datetime] = created()
    last_seen_at: Mapped[datetime] = created()
    ingest_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_url: Mapped[str] = mapped_column(String(255), default="")
    method: Mapped[str] = mapped_column(String(30), default="")  # jsonld | adapter | csv
    started_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # pending -> ready (diff awaiting review) -> published, or failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    listings_found: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    removed_count: Mapped[int] = mapped_column(Integer, default=0)
    diff_json: Mapped[str] = mapped_column(Text, default="{}")
    errors_json: Mapped[str] = mapped_column(Text, default="[]")


class VehicleMention(Base):
    """Every time Liner names a vehicle. Powers 'quoted 6 times' and the
    blast-radius panel when a car turns out to be sold (§18.2)."""

    __tablename__ = "vehicle_mentions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.id"), index=True)
    quoted_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = created()
