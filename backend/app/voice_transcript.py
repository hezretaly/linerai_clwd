"""Building a call's transcript out of two things known very differently.

Liner's half is not a transcription. `response.output_audio_transcript.done`
is the model's own text, emitted alongside the audio it spoke, so it is exact
-- there is nothing to recover and nothing to get wrong. The buyer's half is a
guess made by a separate model from a microphone, and on a phone call that
guess is frequently poor and occasionally in the wrong language entirely.

So the two are captured apart and joined on time. Every segment carries an
offset in milliseconds from the first slice of audio -- one clock, stamped in
the browser, which is the only place that can see both halves happen. Server
receipt time cannot do this job at all: the live transcriber runs with
`delay: high`, so the buyer's question arrives *after* the answer to it, and a
transcript ordered by arrival shows Liner replying before it was asked. That
is not a hypothetical; it is what the current transcripts look like.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.db import utcnow
from app.events import emit
from app.models import CallBuyerTrack, CallRecording, CallSegment, Conversation, Message

#: How far a transcribed span may sit from any detected speech and still be
#: taken as the buyer talking.
#:
#: The provider's turn detector fires *after* speech has begun and releases
#: well after it ends, so a span is always late at the start and long at the
#: end. The padding is deliberately generous because of what the two errors
#: cost: too tight and a sentence the buyer really said is thrown away, which
#: this codebase treats as far worse than keeping one they did not. All this
#: needs to catch is audio nowhere near any speech at all -- the model's own
#: voice coming back in through a laptop speaker, and room noise given words.
CROSSTALK_PAD_MS = 2000


def record_segments(db: Session, convo: Conversation, segments: list[dict]) -> int:
    """Store marks as the call produces them.

    Written as they close rather than posted in a batch at the end, for the
    same reason the audio is: the end of a call is the least reliable moment
    there is. A crashed tab loses the last mark instead of the timeline.
    """
    written = 0
    for raw in segments:
        speaker = str(raw.get("speaker") or "").strip()
        if speaker not in {"buyer", "assistant"}:
            continue
        started = max(int(raw.get("started_ms") or 0), 0)
        ended = max(int(raw.get("ended_ms") or 0), started)
        text = (raw.get("text") or "").strip()
        source = str(raw.get("source") or "").strip() or ("model" if text else "vad")
        # Liner's own words arrive with the mark; the buyer's are recovered
        # later from the audio, so an empty buyer segment is a stage rather
        # than a failure. An empty *assistant* segment is neither -- there is
        # no later pass that could fill it, so it is nothing at all.
        if speaker == "assistant" and not text:
            continue
        db.add(CallSegment(
            conversation_id=convo.id, speaker=speaker, started_ms=started,
            ended_ms=ended, text=text, source=source,
        ))
        written += 1
    if written:
        db.commit()
    return written


def _overlaps(started: int, ended: int, spans: list[tuple[int, int]]) -> bool:
    for lo, hi in spans:
        if started <= hi + CROSSTALK_PAD_MS and ended >= lo - CROSSTALK_PAD_MS:
            return True
    return False


def apply_transcription(db: Session, convo: Conversation, spans: list) -> dict:
    """Fold a transcription of the buyer's track into the call's timeline.

    The transcription is authoritative for *what* the buyer said. The marks
    already stored are authoritative for *when* anyone spoke, and for which
    stretches of the buyer's track are the buyer at all -- a laptop speaker
    puts Liner's voice back into the microphone, and a transcriber will
    happily give those words to the buyer.

    That filter only runs when there are marks to run it against. A call whose
    marks never arrived should get a worse transcript, not an empty one.
    """
    existing = (
        db.query(CallSegment)
        .filter_by(conversation_id=convo.id)
        .order_by(CallSegment.started_ms.asc())
        .all()
    )
    heard = [(s.started_ms, s.ended_ms) for s in existing if s.speaker == "buyer"]

    kept, dropped = [], 0
    for span in spans:
        if heard and not _overlaps(span.started_ms, span.ended_ms, heard):
            dropped += 1
            continue
        kept.append(span)

    # The detected spans were placeholders for exactly these words. Replacing
    # them rather than adding alongside, because two rows for one utterance is
    # a transcript that says everything twice.
    for row in existing:
        if row.speaker == "buyer":
            db.delete(row)
    db.flush()

    for span in kept:
        db.add(CallSegment(
            conversation_id=convo.id, speaker="buyer",
            started_ms=span.started_ms, ended_ms=span.ended_ms,
            text=span.text, source="recorded",
        ))
    db.flush()

    rebuilt = _rebuild_messages(db, convo)
    db.commit()
    emit(db, "call.transcribed", {
        "conversation_id": convo.id, "lines": rebuilt, "buyer_spans": len(kept),
    })
    return {"kept": len(kept), "dropped": dropped, "messages": rebuilt}


def _rebuild_messages(db: Session, convo: Conversation) -> int:
    """Rewrite the call's transcript from its segments, in the order spoken.

    All or nothing, and only when Liner's marks are there. Replacing just the
    buyer's lines would leave the two halves stamped from different clocks --
    one from the marks, one from server receipt -- which is the misordering
    this whole exercise exists to fix, reintroduced at the join.

    A `rep` message is never touched. That is a person typing into the thread
    after the fact, and it belongs to no point in the audio.
    """
    segments = (
        db.query(CallSegment)
        .filter_by(conversation_id=convo.id)
        .order_by(CallSegment.started_ms.asc(), CallSegment.created_at.asc())
        .all()
    )
    if not any(s.speaker == "assistant" for s in segments):
        return 0
    if not any(s.speaker == "buyer" and s.text for s in segments):
        return 0

    base = _audio_started(db, convo)
    if base is None:
        return 0

    (
        db.query(Message)
        .filter(Message.conversation_id == convo.id, Message.role.in_(["buyer", "assistant"]))
        .delete(synchronize_session=False)
    )
    db.flush()

    written = 0
    for segment in segments:
        if not segment.text:
            continue
        db.add(Message(
            conversation_id=convo.id,
            role=segment.speaker,
            content=segment.text,
            created_at=base + timedelta(milliseconds=segment.started_ms),
        ))
        written += 1
    db.flush()
    return written


def _audio_started(db: Session, convo: Conversation):
    """When the offsets are measured from.

    The earliest audio row for this call, because both recorders are started
    in the same statement and the first slice of each lands moments later. Any
    consistent base gives the right *order*, which is what a transcript needs;
    this one also puts the absolute times within a couple of seconds.
    """
    stamps = [
        row.created_at
        for row in (
            db.query(CallRecording).filter_by(conversation_id=convo.id).one_or_none(),
            db.query(CallBuyerTrack).filter_by(conversation_id=convo.id).one_or_none(),
        )
        if row is not None
    ]
    return min(stamps) if stamps else None


def merged(db: Session, convo: Conversation) -> list[dict]:
    """The call as one ordered list, whatever stage it is at.

    Falls back to `messages` when a call has no marks -- every call recorded
    before this existed, and any call whose data channel dropped them. A
    transcript that is merely in arrival order is worth showing; an empty one
    is not.
    """
    segments = (
        db.query(CallSegment)
        .filter_by(conversation_id=convo.id)
        .order_by(CallSegment.started_ms.asc(), CallSegment.created_at.asc())
        .all()
    )
    if not segments:
        rows = (
            db.query(Message)
            .filter(Message.conversation_id == convo.id, Message.role.in_(["buyer", "assistant"]))
            .order_by(Message.created_at.asc())
            .all()
        )
        return [
            {"speaker": m.role, "text": m.content, "started_ms": None, "source": "live"}
            for m in rows
        ]
    return [
        {
            "speaker": s.speaker,
            "text": s.text,
            "started_ms": s.started_ms,
            "source": s.source,
        }
        for s in segments
        if s.text
    ]


def mark_transcribed(db: Session, track: CallBuyerTrack) -> None:
    track.transcribed_at = utcnow()
    db.commit()
