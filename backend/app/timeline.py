"""One ordered timeline for a buyer, across every channel they used.

The dashboard used to be organised by thread: a chat here, a call there, email
somewhere else. A buyer who chats at 9pm and calls back next morning was three
unrelated screens, and a rep could ring someone who had already booked.

Combining them is a query rather than a migration, because the schema already
agrees with the idea:

* voice transcript chunks land in ``messages`` like chat turns
  (``api/voice.py``), so a call and a chat are the same rows with a different
  ``conversations.channel``;
* ``outreach`` already hangs off the lead, not off a conversation.

Composed here, on the server, for the reason ``recap.py`` is: ordering and
de-duplication get decided once, against rows, instead of four arrays being
merged and re-sorted by whichever client asked.

**The de-duplication is the whole difficulty.** ``api/outreach.py`` mirrors an
appointment email into the buyer's thread as a ``role="rep"`` message carrying
``{"name": "outreach", "outreach_id": ...}``, so that the round trip lands
visibly without depending on inbox delivery. Lead-level outreach -- the
follow-up and credit-application composers -- has no mirror. Concatenating
``messages`` and ``outreach`` therefore shows every appointment email twice and
every follow-up once, which reads as a system that sent things it did not send.
So a mirror and its row are folded into a single entry keyed on the outreach
id, and an unmirrored row stands on its own.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import (
    Appointment,
    CallRecording,
    Conversation,
    Escalation,
    Lead,
    Message,
    Outreach,
    User,
    Vehicle,
)
from app.schemas.serialize import iso, loads, outreach_out, stamp, user_out, vehicle_out

# Within the same second, what a rep expects to read first. A booking is the
# consequence of the message above it, not the other way round.
# A call opens before anything said on it, so it sorts first within a second.
KIND_ORDER = {"call": 0, "message": 1, "outreach": 2, "appointment": 3,
              "escalation": 4}


def _mirrored_outreach_id(message: Message) -> str | None:
    """The outreach this thread message is a copy of, if it is one."""
    for call in loads(message.tool_calls_json, []):
        if isinstance(call, dict) and call.get("outreach_id"):
            return str(call["outreach_id"])
    return None


def _entry(kind: str, at: datetime | None, **payload) -> dict:
    return {"kind": kind, "at": stamp(at), **payload}


def compose(
    db: Session,
    conversations: list[Conversation],
    *,
    outreach: list[Outreach] | None = None,
    appointments: list[Appointment] | None = None,
) -> list[dict]:
    """Every entry for these conversations, oldest first."""
    convo_by_id = {c.id: c for c in conversations}
    ids = list(convo_by_id)

    messages = (
        db.query(Message).filter(Message.conversation_id.in_(ids)).all() if ids else []
    )
    # Which outreach rows already appear in a thread, and where.
    mirrors: dict[str, Message] = {}
    for m in messages:
        found = _mirrored_outreach_id(m)
        if found:
            mirrors[found] = m

    entries: list[dict] = []

    # One entry per call, at the moment it started, carrying the two things a
    # transcript cannot: how long it ran, and whether the audio is there to
    # play. Per conversation rather than per message because that is what a
    # recording is -- a rep scanning a buyer's history wants "an eight-minute
    # call on Tuesday" as one item, not a header over forty transcript lines.
    recorded = {
        r.conversation_id: r
        for r in (
            db.query(CallRecording).filter(CallRecording.conversation_id.in_(ids)).all()
            if ids else []
        )
    }
    for c in conversations:
        if c.channel != "voice":
            continue
        audio = recorded.get(c.id)
        entries.append(_entry(
            "call", c.started_at,
            id=c.id,
            channel="voice",
            conversation_id=c.id,
            # From the row, not from the audio: a call whose recording failed
            # still lasted as long as it lasted.
            seconds=(
                int((c.ended_at - c.started_at).total_seconds())
                if c.ended_at else 0
            ),
            live=c.ended_at is None,
            has_recording=(
                audio is not None and audio.size_bytes > 0
                # A row from the version that named every file "None.webm"
                # points at everybody's audio, so it is offered as nobody's.
                and bool(audio.filename) and not audio.filename.startswith("None.")
            ),
            recording_seconds=round(audio.duration_ms / 1000) if audio else 0,
            # A recording is finished when the call said so. Zero means the
            # slices stopped arriving without an end marker -- a crashed tab,
            # a killed browser -- so the file is a real but partial call, and
            # offering it as though it were whole would misrepresent it.
            recording_complete=bool(audio and audio.duration_ms),
            # Whether the buyer's half was ever written down. With
            # transcription off a call leaves Liner's lines and nothing else,
            # which reads exactly like an assistant talking to itself -- and a
            # rep who cannot tell the difference will either distrust a working
            # call or miss a broken one.
            both_sides=any(
                m.role == "buyer" for m in messages if m.conversation_id == c.id
            ) or c.ended_at is None,
        ))

    for m in messages:
        if _mirrored_outreach_id(m):
            continue  # emitted below as the outreach it copies
        convo = convo_by_id[m.conversation_id]
        entries.append(_entry(
            "message", m.created_at,
            id=m.id,
            channel=convo.channel,
            conversation_id=convo.id,
            role=m.role,
            content=m.content,
            tool_calls=loads(m.tool_calls_json, []),
        ))

    for o in outreach or []:
        # An inbound reply has no mirror by definition: nothing wrote it into a
        # thread, a buyer sent it to us.
        mirror = mirrors.get(o.id) if o.direction != "in" else None
        # The mirror's timestamp, when there is one: that is where the email
        # sits in the thread a rep is reading, and moving it by a few
        # milliseconds would shuffle it past the message it answered.
        at = mirror.created_at if mirror is not None else (o.sent_at or o.created_at)
        row = outreach_out(o)
        # An outreach row has a `kind` of its own -- followup, reminder,
        # credit_application -- and so does a timeline entry. Two different
        # words for two different things, and letting them share a key means
        # the spread silently overwrites which sort of entry this is.
        row["outreach_kind"] = row.pop("kind")
        row.pop("created_at", None)
        entries.append(_entry(
            "outreach", at,
            conversation_id=mirror.conversation_id if mirror is not None else None,
            in_thread=mirror is not None,
            # `channel` rides along from outreach_out -- 'email', or
            # 'phone_logged' for a call a rep wrote up. Both are real things
            # that happened, and the filter strip names them from this.
            **row,
        ))

    for a in appointments or []:
        vehicle = (
            db.query(Vehicle).filter_by(id=a.vehicle_id).one_or_none()
            if a.vehicle_id else None
        )
        entries.append(_entry(
            "appointment", a.created_at,
            id=a.id,
            # Not a channel. The filter strip slices what was *said*; an
            # appointment happened regardless of where it was arranged.
            channel="",
            conversation_id=a.conversation_id,
            starts_at=iso(a.starts_at),
            status=a.status,
            booked_by=a.booked_by,
            vehicle=vehicle_out(vehicle) if vehicle else None,
        ))

    escalations = (
        db.query(Escalation).filter(Escalation.conversation_id.in_(ids)).all() if ids else []
    )
    for e in escalations:
        claimed = (
            db.query(User).filter_by(id=e.claimed_by_user_id).one_or_none()
            if e.claimed_by_user_id else None
        )
        entries.append(_entry(
            "escalation", e.created_at,
            id=e.id,
            channel="",
            conversation_id=e.conversation_id,
            reason=e.reason,
            claimed_at=stamp(e.claimed_at),
            claimed_by=user_out(claimed) if claimed else None,
        ))

    entries.sort(key=lambda x: (x["at"] or "", KIND_ORDER.get(x["kind"], 9)))
    return entries


def lead_timeline(db: Session, lead: Lead) -> list[dict]:
    """Everything this buyer did, on every channel."""
    conversations = db.query(Conversation).filter_by(lead_id=lead.id).all()
    return compose(
        db,
        conversations,
        outreach=db.query(Outreach).filter_by(lead_id=lead.id).all(),
        appointments=db.query(Appointment).filter_by(lead_id=lead.id).all(),
    )


def conversation_timeline(db: Session, convo: Conversation) -> list[dict]:
    """One thread. For a conversation that has no lead yet -- an anonymous chat
    is still something a rep has to be able to read and answer, and it has no
    buyer to hang a timeline on until someone books."""
    return compose(
        db,
        [convo],
        outreach=[],
        appointments=db.query(Appointment).filter_by(conversation_id=convo.id).all(),
    )


def channel_counts(entries: list[dict]) -> dict[str, int]:
    """What the filter strip offers, built from what is actually here.

    Never a fixed list of channels. SMS has no provider in this system, and a
    tab that is always empty claims a capability that does not exist.

    **It counts conversations, not turns.** This used to count entries, so a
    single eight-minute call with sixteen transcript lines read `Voice call 17`
    -- directly under a header saying `1 thread`. Nobody reads that number as
    "lines of transcript"; it says seventeen phone calls, and a manager
    deciding who to ring next is reading it as how much this buyer has already
    been through.

    One per conversation, then, and one per email -- the unit is a time
    somebody made contact, which is what the label already implies. The rows
    the tab then shows are the detail inside those contacts, and there are
    naturally more of them.
    """
    counts: dict[str, int] = {}
    threads: dict[str, set[str | None]] = {}
    for entry in entries:
        channel = entry.get("channel") or ""
        if not channel:
            # Appointments and escalations happened regardless of where they
            # were arranged, so they belong to no channel and no tab.
            continue
        # Each email is its own contact -- there is no thread to fold them
        # into, and two emails on one day are two times we wrote to somebody.
        if entry.get("kind") == "outreach":
            counts[channel] = counts.get(channel, 0) + 1
            continue
        thread = entry.get("conversation_id")
        seen = threads.setdefault(channel, set())
        if thread in seen:
            continue
        seen.add(thread)
        counts[channel] = counts.get(channel, 0) + 1
    return counts
