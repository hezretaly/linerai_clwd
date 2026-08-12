"""SQLAlchemy models -- the 17 tables of §4.1.

Two conventions hold everywhere so the Postgres door stays open: string UUID
primary keys and naive-UTC ``TIMESTAMP`` columns. The one exception is
``events.id``, which is an autoincrement integer on purpose -- the WebSocket
replays with ``?since={event_id}`` and needs a monotonic cursor. That maps to
``BIGSERIAL`` on Postgres.
"""

from app.models.crm import (
    PROVENANCE,
    STAGES,
    Appointment,
    CapturedField,
    Conversation,
    Escalation,
    Event,
    CallUsage,
    InboundEmail,
    Lead,
    Message,
    Outreach,
)
from app.models.dealership import (
    AssistantSettings,
    Dealership,
    HandoffRule,
    KnowledgeEntry,
    Rail,
    User,
)
from app.models.inventory import IngestRun, Vehicle, VehicleMention

__all__ = [
    "PROVENANCE",
    "STAGES",
    "Appointment",
    "AssistantSettings",
    "CapturedField",
    "Conversation",
    "Dealership",
    "Escalation",
    "Event",
    "CallUsage",
    "InboundEmail",
    "HandoffRule",
    "IngestRun",
    "KnowledgeEntry",
    "Lead",
    "Message",
    "Outreach",
    "Rail",
    "User",
    "Vehicle",
    "VehicleMention",
]
