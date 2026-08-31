"""SQLAlchemy models -- the 17 tables of §4.1.

Two conventions hold everywhere so the Postgres door stays open: string UUID
primary keys and naive-UTC ``TIMESTAMP`` columns. The one exception is
``events.id``, which is an autoincrement integer on purpose -- the WebSocket
replays with ``?since={event_id}`` and needs a monotonic cursor. That maps to
``BIGSERIAL`` on Postgres.
"""

from app.models.crm import (
    LeadAddress,
    PROVENANCE,
    STAGES,
    Appointment,
    CapturedField,
    Conversation,
    Escalation,
    Event,
    CallBuyerTrack,
    CallRecording,
    CallSegment,
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
# Ours, in their own tables. Imported last so the `ops_` prefix is the
# first thing anyone reading this list notices about them.
from app.models.ops import (
    OPS_TABLES,
    DemoRequest,
    OpsMailState,
    OpsMessage,
    OpsUser,
)

__all__ = [
    "OPS_TABLES",
    "OpsMailState",
    "OpsMessage",
    "OpsUser",
    "PROVENANCE",
    "STAGES",
    "Appointment",
    "AssistantSettings",
    "CapturedField",
    "Conversation",
    "Dealership",
    "Escalation",
    "Event",
    "CallBuyerTrack",
    "CallRecording",
    "CallSegment",
    "DemoRequest",
    "CallUsage",
    "InboundEmail",
    "HandoffRule",
    "IngestRun",
    "KnowledgeEntry",
    "Lead",
    "LeadAddress",
    "Message",
    "Outreach",
    "Rail",
    "User",
    "Vehicle",
    "VehicleMention",
]
