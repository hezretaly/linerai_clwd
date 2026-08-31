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

# The first four are conversational and are the only ones `save_captured_fields`
# will accept -- the agent must not be able to claim a field came from a feed.
# 'adf' is written solely by the lead importer, from a document a dealer uploaded.
PROVENANCE = ["typed", "listing", "caller_id", "inferred", "adf"]


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    phone: Mapped[str] = mapped_column(String(40), default="")
    # chat|phone|website|adf -- 'adf' means it arrived as a marketplace lead
    # document rather than from a conversation this system had.
    source: Mapped[str] = mapped_column(String(20), default="chat")
    assigned_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    email_consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created()

    @property
    def contact_risk(self) -> bool:
        """No email on file means the product cannot reach them (§18.5)."""
        return not self.email


class LeadAddress(Base):
    """A second address the same buyer writes from, attached by a person.

    `leads.email` is one column and a buyer is not. Somebody who chatted from
    `dana@work.example` and later mails from `dana@home.example` is one person
    that no rule here can see: matching is email exact and phone by its last
    ten digits, deliberately, because a name is not identity and two Dave
    Joneses are two people. So the join is a **human act** rather than a
    guess -- a rep who knows says so, and this is where they say it.

    A table rather than a column, and not only because `create_all` adds one
    and never the other: a buyer can have several, and the rep who attached it
    is worth keeping. It is the answer to "why is this mail on this timeline"
    when somebody asks in six months.
    """

    __tablename__ = "lead_addresses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"), index=True)
    #: Stored lowercase. `leads.email` is compared the same way, and a mail
    #: server is entitled to rewrite the case of a local part.
    address: Mapped[str] = mapped_column(String(255), index=True)
    added_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = created()


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
    # How it ended, when that is worth knowing. "" while it is still running or
    # simply ran out; "declined" when the buyer said no. Separate from status
    # because "closed" already means several different things and a queue that
    # cannot tell them apart cannot be filtered.
    outcome: Mapped[str] = mapped_column(String(20), default="")


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
    # Which way it went. Inbound replies are the same shape as outbound sends
    # -- an address, a subject, a body, a provider id -- so they share the
    # table rather than getting one of their own, and the timeline renders
    # both without a new entry kind.
    direction: Mapped[str] = mapped_column(String(3), default="out", index=True)  # out | in
    # What this send *was*, so the overview can count one kind without
    # inferring it from the subject line. followup | reminder | credit_application
    kind: Mapped[str] = mapped_column(String(30), default="followup", index=True)
    to_address: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(30), default="")
    provider_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_thread_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|sent|bounced|failed
    error: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Click tracking, and only what a redirect can honestly know. A link to
    # the dealer's own site is invisible to us, so the send rewrites it to a
    # /r/<token> hop we own. No token means the body carried no trackable
    # link -- a rep who deleted it, or a send made before this existed --
    # which is different from a link nobody clicked.
    # What makes a reply traceable. Every outbound send carries
    # `Reply-To: reply+<reply_token>@<sending_domain>`, and the Cloudflare
    # catch-all routes that straight back to the row that sent it. Keyed on
    # the send rather than on a conversation because most outreach here is
    # composed against a lead and has no conversation to name.
    reply_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # The provider id of the message this one answers, for the fallback path
    # when a reply arrives without the plus-address (a client that rewrites
    # Reply-To, or a forward).
    in_reply_to: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    click_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    click_count: Mapped[int] = mapped_column(Integer, default=0)
    first_clicked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_clicked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = created()


class InboundEmail(Base):
    """A receipt for every delivery the webhook was handed, kept or not.

    Append-only, the same instinct as ``events``. It does three jobs at once:
    it is the dedupe index on ``message_id``, it is the log the setup page
    reads, and it is the only way to debug a delivery that was refused --
    without it a bad signature returns 401 into the void and the operator has
    nothing to look at.

    Storing an unresolved message is deliberate. A reply this system cannot
    place is still a real buyer talking, and dropping it silently is the worst
    of the available answers.
    """

    __tablename__ = "inbound_emails"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # accepted | duplicate | unresolved | bad_signature | malformed
    outcome: Mapped[str] = mapped_column(String(20), index=True)
    message_id: Mapped[str] = mapped_column(String(200), default="", index=True)
    from_address: Mapped[str] = mapped_column(String(255), default="")
    to_address: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    in_reply_to: Mapped[str] = mapped_column(String(200), default="")
    # How it was placed, when it was: reply_token | in_reply_to | from_address.
    # A rep looking at a misfiled reply needs to know which rule put it there.
    matched_by: Mapped[str] = mapped_column(String(20), default="")
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    outreach_id: Mapped[str | None] = mapped_column(ForeignKey("outreach.id"), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created()


class CallUsage(Base):
    """What one response on a voice call actually cost, in tokens.

    A separate table rather than columns on `conversations` for two reasons.
    A call bills per response and the interesting question is where inside a
    call the money went -- a total hides that the eleventh turn cost six times
    the second. And a new table is created by `create_all` on an existing
    database, where a new column is not: no reset, no lost data.

    Recorded rather than estimated. The realtime API returns a `usage` object
    on every `response.done`, and the whole reason this exists is that "about
    twenty-five cents a minute" is a number nobody can act on. Cached input is
    kept separate from fresh input because that split *is* the bill: cached
    tokens are discounted by roughly eighty times, so a call where caching
    stopped hitting costs several times one where it did, with nothing else
    different.
    """

    __tablename__ = "call_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    # The provider's id for the response this bills, so a retried relay is
    # recorded once rather than doubling a call's apparent cost.
    response_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    model: Mapped[str] = mapped_column(String(60), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    input_audio_tokens: Mapped[int] = mapped_column(Integer, default=0)
    input_text_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_audio_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_audio_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_text_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created()


class CallRecording(Base):
    """The audio of one call, on disk, with a row pointing at it.

    Bytes on disk rather than in the database: a five-minute call is a few
    megabytes, and a table that grows by megabytes a row turns every backup and
    every `SELECT *` into a bad afternoon. The row carries what the file is,
    because that is not guessable -- Safari's MediaRecorder produces mp4 and
    Chrome's produces webm, and serving one as the other plays silence.

    One per conversation. A second upload for a call that already has audio is
    refused rather than appended: an unauthenticated endpoint that accepts
    unlimited bytes against one id is a place to store someone else's files.
    """

    __tablename__ = "call_recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True, unique=True
    )
    filename: Mapped[str] = mapped_column(String(120), default="")
    content_type: Mapped[str] = mapped_column(String(60), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # What the browser measured, which is the length of the audio rather than
    # the length of the conversation row -- they differ when a tab is left open
    # after the goodbye.
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created()


class CallBuyerTrack(Base):
    """The buyer's microphone alone, kept so the call can be transcribed
    properly after it ends.

    Not a second copy of the recording and never served for playback: the mix
    in `call_recordings` is the record a rep listens to, and offering two
    players for one call is a question nobody should have to answer. This is a
    working file with one job -- a channel containing exactly one speaker, so a
    transcription of it cannot mis-attribute a line.

    That single-speaker property is the whole value. The mix cannot be
    transcribed usefully: both halves are on one track, so the result is an
    undifferentiated stream of words with no way to tell who said which, which
    is worse than the half-transcript it would replace.

    Its own table rather than a row in `call_recordings`, because
    `conversation_id` is unique there and widening it would need a migration
    this codebase does not have. A new table `create_all` simply makes.
    """

    __tablename__ = "call_buyer_tracks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True, unique=True
    )
    filename: Mapped[str] = mapped_column(String(120), default="")
    content_type: Mapped[str] = mapped_column(String(60), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Stamped when a transcription of this file has been written back. Also the
    # guard that stops one running twice: the second pass would delete the
    # lines the first produced and pay to make them again.
    transcribed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = created()


class CallSegment(Base):
    """Who spoke, when, and what they said -- one row per stretch of speech.

    This is the call's timeline, and it exists because the two halves of a call
    are known with very different confidence. Liner's words are *not* a
    transcription: `response.output_audio_transcript.done` is the model's own
    text, generated alongside the audio it spoke, and therefore exact. The
    buyer's words come from a separate transcription model that mishears, and
    on this model family sometimes mis-detects the language entirely.

    So the two are captured differently and joined on time. The browser stamps
    every segment as an offset from the first byte of audio -- one clock, its
    own -- and that is what makes the merge trustworthy. Server receipt time
    cannot do this job: the transcriber runs with `delay: high`, so the buyer's
    line for a question arrives *after* the answer to it, and a transcript
    ordered by arrival shows Liner replying before it was asked.

    `text` is empty on a buyer segment until it has been transcribed. That is
    not a missing value, it is a stage: the span was detected live by the
    provider's own turn detector, and the words are recovered afterwards from
    the audio.
    """

    __tablename__ = "call_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    speaker: Mapped[str] = mapped_column(String(12), default="buyer")
    # Milliseconds from the start of the recording, not a wall clock. The
    # recording is the only timeline both halves are certainly on.
    started_ms: Mapped[int] = mapped_column(Integer, default=0, index=True)
    ended_ms: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    #: `model` -- Liner's own words, exact. `vad` -- a detected span of buyer
    #: speech with the words not yet recovered. `live` -- the streaming
    #: transcriber's guess. `recorded` -- transcribed from the buyer's track
    #: after the call, which is the one to trust.
    source: Mapped[str] = mapped_column(String(12), default="vad")
    created_at: Mapped[datetime] = created()


class Event(Base):
    """Append-only. Doubles as the audit log and the WebSocket reconnect buffer."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(60), index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = created()
