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

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app import email_envelopes, outreach_status
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

# ---------------------------------------------------------------------------
# What counts as "we were in contact with this buyer" -- the one definition
# `channel_counts` uses for the strip's `All`, `leads.py`'s channel list and
# `last_touch_at`, and the Overview's channel KPIs each used to answer
# separately (item 21).
#
# `Outreach.channel` also holds 'phone_logged' for a rep-written-up call, and
# both a text and an email can be inbound or outbound -- so "is this row a
# contact" needs the same three facts the Mail page already reads for email:
# is it a channel a person could have actually used, and did an outbound one
# actually go (an inbound row is contact by definition; it arrived).
# ---------------------------------------------------------------------------

CONTACT_CHANNELS = ("email", "sms", "phone_logged")


def outreach_is_contact(channel: str, direction: str, status: str) -> bool:
    """True for an Outreach row that is real contact with a buyer: on a
    channel that counts, and -- if it went outbound -- one that actually
    reached them. A queued or failed outbound send never landed; a logged
    call and an inbound message always count, because both are real by
    construction (a rep only logs a call that happened; an inbound row
    arrived)."""
    return channel in CONTACT_CHANNELS and (
        direction == "in" or outreach_status.went_out(direction, status)
    )


#: The SQL twin of `outreach_is_contact`, for a query-side filter.
def contact_clause():
    from app.models import Outreach

    return and_(
        Outreach.channel.in_(CONTACT_CHANNELS),
        or_(Outreach.direction == "in", outreach_status.WENT_OUT),
    )


def contact_at(o) -> datetime:
    """When a contact row happened, for sorting/last-touch purposes -- the
    same `sent_at or created_at` rule the Mail page dates a row by."""
    return outreach_status.sent_at(o)


def last_heard_query(db: Session):
    """A grouped `(lead_id, at)` query: the last time we actually heard from
    each buyer -- something *they* wrote, never our own reply.

    `campaigns.py`'s "gone cold" card used to take the newest `Message` of
    *any* role in a buyer's threads, and Liner's own reply is always the
    newest row in an answered thread -- so "last heard from them" was really
    "we last spoke", one step later than the truth, and dated in the naive
    UTC `str(at)[:10]`, which is already tomorrow's date in the evening at a
    dealership west of Greenwich (item 40).

    Two sources, unioned: a buyer's own chat/voice/email message
    (`Message.role == "buyer"`), and an inbound email or text
    (`Outreach.direction == "in"`) -- covering a channel (SMS) that writes no
    `Message` at all. `outreach_status.SENT_AT` dates the second the same way
    the Mail page dates any other outreach row.
    """
    from app.models import Conversation, Outreach

    buyer_messages = (
        db.query(
            Conversation.lead_id.label("lead_id"),
            Message.created_at.label("at"),
        )
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(Conversation.lead_id.is_not(None), Message.role == "buyer")
    )
    inbound_outreach = db.query(
        Outreach.lead_id.label("lead_id"),
        outreach_status.SENT_AT.label("at"),
    ).filter(Outreach.lead_id.is_not(None), Outreach.direction == "in")
    combined = buyer_messages.union_all(inbound_outreach).subquery()
    return (
        db.query(combined.c.lead_id, func.max(combined.c.at).label("at"))
        .group_by(combined.c.lead_id)
    )


def last_heard_at(db: Session, lead_ids: list[str]) -> dict[str, datetime]:
    """Per-lead wrapper over `last_heard_query`, for a caller holding a
    handful of ids rather than building a campaign audience from scratch."""
    if not lead_ids:
        return {}
    q = last_heard_query(db).subquery()
    return dict(
        db.query(q.c.lead_id, q.c.at).filter(q.c.lead_id.in_(lead_ids)).all()
    )


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

    # What each email carried beyond one address -- every recipient, the
    # files, importance -- loaded for the whole timeline at once. A buyer a
    # year in has hundreds of these, and a query per card is the difference
    # between a page that opens and one that hangs.
    envelopes = email_envelopes.for_outreach_many(db, outreach or [])
    files = email_envelopes.attachments_of(db, [e.id for e in envelopes.values()])

    for o in outreach or []:
        # An inbound reply has no mirror by definition: nothing wrote it into a
        # thread, a buyer sent it to us.
        mirror = mirrors.get(o.id) if o.direction != "in" else None
        # The mirror's timestamp, when there is one: that is where the email
        # sits in the thread a rep is reading, and moving it by a few
        # milliseconds would shuffle it past the message it answered.
        at = mirror.created_at if mirror is not None else (o.sent_at or o.created_at)
        env = envelopes.get(o.id)
        # Email only: a text or a logged call has no envelope, and an empty
        # recipient list on one would read as a message sent to nobody.
        row = outreach_out(
            o,
            email=email_envelopes.summary(
                env, files.get(env.id, []) if env else [], include_bcc=True,
            ) if o.channel == "email" else None,
        )
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

    Never a fixed list of channels, and SMS is what that rule was written for.
    There was no SMS provider at all when this was written, so a declared tab
    would have sat at zero forever; now there is one, and the tab appears the
    moment a buyer actually texts -- because a text is an `outreach` row with
    `channel="sms"` and this counts what is there. Nothing was added here to
    make that work, which is the point.

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

    **Email never adds a second, per-conversation unit.** The per-conversation
    branch below exists for chat and voice, where a contact *is* a
    conversation -- but every real email is already counted once as its own
    `outreach` entry, so applying that branch to email double-counted: an
    email conversation holding even one message `compose()` could not fold
    into its `outreach` card (Liner's own auto-reply, before it was mirrored
    below) added a phantom extra contact on top of the emails actually sent.
    `email_threads.py` -- the one place email exchanges are counted for the
    Mail page -- counts one unit per `Outreach` row and nothing else; this
    matches it (items 20, 32).

    **An outreach entry only counts when it was real contact.** A queued or
    failed outbound send, or a logged call/text on a channel it does not
    belong to, is not something the buyer actually received --
    `outreach_is_contact` is the same predicate `leads.py`'s channel list and
    `overview.py`'s "Emails sent" KPI now read, so a failed send does not add
    a tab here that the Overview would refuse to count as a send (item 21).
    """
    counts: dict[str, int] = {}
    threads: dict[str, set[str | None]] = {}
    for entry in entries:
        channel = entry.get("channel") or ""
        if not channel:
            # Appointments and escalations happened regardless of where they
            # were arranged, so they belong to no channel and no tab.
            continue
        # Each real send/receipt is its own contact -- there is no thread to
        # fold them into, and two emails on one day are two times we wrote to
        # somebody. A row that never actually reached them is not one.
        if entry.get("kind") == "outreach":
            if not outreach_is_contact(
                channel, entry.get("direction") or "", entry.get("status") or ""
            ):
                continue
            counts[channel] = counts.get(channel, 0) + 1
            continue
        if channel == "email":
            # Every email is already counted above, as its outreach row --
            # a message entry on the email channel is a mirror (already
            # folded into that row by compose()) or, on old data, an
            # unmirrored stray. Either way it is not a second contact.
            continue
        thread = entry.get("conversation_id")
        seen = threads.setdefault(channel, set())
        if thread in seen:
            continue
        seen.add(thread)
        counts[channel] = counts.get(channel, 0) + 1
    return counts
