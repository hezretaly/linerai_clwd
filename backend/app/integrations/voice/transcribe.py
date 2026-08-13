"""Transcribing the buyer's track after the call has ended.

# PLACEHOLDER(openai-transcriptions): the HTTP call has never run here. There
# is no OPENAI_API_KEY in this environment and `api.openai.com` is refused by
# the egress proxy besides. Everything either side of that one request is real
# and driven by `make smoke` through `ScriptedTranscriber`: the multipart body
# this builds, the segment parsing, the merge back into the call's timeline,
# the message rewrite and the once-only guard.

**Why there are two transcriptions of the same call.** The live one, on the
realtime session, exists to put words on the rail while a rep is watching. It
runs on a stream, with no future context and no second pass, and on this model
family it sometimes mis-detects English outright -- `比克拉斯` for "E-Class".
This one runs afterwards on a complete file with the whole call as context,
can be retried, and is the version worth keeping.

**Only the buyer's half is transcribed, ever.** Liner's words are not a
transcription at all: `response.output_audio_transcript.done` is the model's
own text, emitted alongside the audio it spoke, so it is exact by
construction. Sending Liner's audio to a transcriber would spend money to make
a *worse* copy of something already known. That asymmetry is the reason the
buyer's microphone is recorded to its own file -- a track with exactly one
speaker on it cannot have a line attributed to the wrong person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import settings
from app.integrations.base import NotConfigured

API = "https://api.openai.com/v1/audio/transcriptions"

#: A ten-minute call is a few megabytes and the vendor's own limit is 25 MB.
#: Refusing early names the reason; posting it and reading a 413 does not.
MAX_UPLOAD = 25 * 1024 * 1024

TIMEOUT = 120.0


@dataclass
class Span:
    """One stretch of speech, in milliseconds from the start of the file."""

    started_ms: int
    ended_ms: int
    text: str


@dataclass
class Transcription:
    spans: list[Span] = field(default_factory=list)
    model: str = ""


class Transcriber:
    """Named separately from the realtime provider because it is a different
    model on a different bill, reached at a different endpoint. Folding it into
    `VoiceProvider` would make "turn the live transcriber off" and "transcribe
    afterwards" look like one setting when they are opposites."""

    name = "unconfigured"

    def check(self) -> None:
        raise NotConfigured(
            "transcription",
            ["OPENAI_API_KEY"],
            "Transcribing a call after it ends needs an API key. It reuses "
            "OPENAI_API_KEY, or VOICE_PROVIDER_KEY if voice bills to a "
            "different project.",
        )

    def transcribe(self, path: Path, keywords: list[str] | None = None) -> Transcription:
        self.check()
        raise AssertionError("unreachable")  # pragma: no cover


class OpenAITranscriber(Transcriber):
    name = "openai"

    def check(self) -> None:
        from app.integrations.voice.openai_realtime import api_key

        if not api_key():
            super().check()

    def payload(self, keywords: list[str] | None = None) -> dict:
        """The form fields, built apart from the request so they can be
        asserted without one being sent -- the same trick `ResendSender.payload`
        and `session_payload` use, and the only honest check available here.

        `verbose_json` is not a preference. The default response is a wall of
        text with no times in it, and without times a transcription cannot be
        interleaved with Liner's turns -- which is the entire point of doing
        this. `segment` granularity rather than `word`: a segment is roughly an
        utterance, which is the unit a transcript line already is.
        """
        form = {
            "model": settings.voice_transcribe_after_model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        if settings.voice_language:
            form["language"] = settings.voice_language
        if keywords:
            # The same vocabulary the live transcriber is given: the
            # dealership's own makes, models and trims, which is exactly what
            # comes back mangled. Sent as a prompt because this endpoint has no
            # `keywords` field -- a documented use of it, and it is the one
            # place a hint like this can be attached at all.
            form["prompt"] = ", ".join(keywords[:100])
        return form

    def transcribe(self, path: Path, keywords: list[str] | None = None) -> Transcription:
        self.check()
        from app.integrations.voice.openai_realtime import api_key

        raw = path.read_bytes()
        if not raw:
            return Transcription(model=settings.voice_transcribe_after_model)
        if len(raw) > MAX_UPLOAD:
            raise NotConfigured(
                "transcription",
                [],
                f"The buyer's track is {len(raw) // 1024 // 1024} MB, over the "
                f"{MAX_UPLOAD // 1024 // 1024} MB the API accepts.",
            )

        response = httpx.post(
            API,
            headers={"Authorization": f"Bearer {api_key()}"},
            data=self.payload(keywords),
            files={"file": (path.name, raw, _mime(path))},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return parse(response.json(), settings.voice_transcribe_after_model)


class ScriptedTranscriber(Transcriber):
    """A transcription handed over instead of fetched.

    Not a simulation standing in for a missing feature -- it is how the half of
    this that *is* ours gets tested. The merge, the ordering against Liner's
    turns, the message rewrite and the once-only guard are all real code with
    real consequences, and none of them should first run in production because
    the only way to reach them was a vendor call.
    """

    name = "scripted"

    def __init__(self, spans: list[Span]) -> None:
        self.spans = spans
        self.calls: list[Path] = []

    def check(self) -> None:
        return None

    def transcribe(self, path: Path, keywords: list[str] | None = None) -> Transcription:
        self.calls.append(path)
        return Transcription(spans=list(self.spans), model=self.name)


def parse(body: dict, model: str) -> Transcription:
    """`verbose_json` into spans, seconds into milliseconds.

    A response with no `segments` is not an error: a file of pure silence
    transcribes to an empty string, which is a true answer about a buyer who
    never spoke.
    """
    spans: list[Span] = []
    for segment in body.get("segments") or []:
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        spans.append(Span(
            started_ms=int(float(segment.get("start") or 0) * 1000),
            ended_ms=int(float(segment.get("end") or 0) * 1000),
            text=text,
        ))
    if not spans and (body.get("text") or "").strip():
        # No timestamps came back -- an older model, or a format that ignored
        # the granularity. One span covering the file is still worth having;
        # it lands in the right place relative to nothing, but the words are
        # the words.
        spans.append(Span(0, 0, body["text"].strip()))
    return Transcription(spans=spans, model=model)


def _mime(path: Path) -> str:
    return {
        ".webm": "audio/webm",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
    }.get(path.suffix, "application/octet-stream")


def get_transcriber() -> Transcriber:
    """Off unless voice is on and the after-the-fact pass is wanted.

    Two settings rather than one because they answer different questions.
    `VOICE_PROVIDER` decides whether this dealership takes calls at all;
    `VOICE_TRANSCRIBE_AFTER` decides whether a finished call is sent back for a
    better transcript, and that is a spend a dealership might reasonably
    decline while still taking calls.
    """
    if not settings.voice_transcribe_after:
        return Transcriber()
    if settings.voice_provider.lower() in {"openai", "openai_realtime"}:
        return OpenAITranscriber()
    return Transcriber()
