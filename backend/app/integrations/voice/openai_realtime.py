"""OpenAI Realtime, over WebRTC.

# PLACEHOLDER(openai-realtime): the mint call has never run here. There is no
# OPENAI_API_KEY in this environment, and `api.openai.com` is refused by the
# egress proxy besides -- `CONNECT tunnel failed, response 403`. Everything
# either side of that one request is real and asserted offline by
# `make agent-check`: the session body, the tool conversion, the voice-only
# instructions, and the fact that `check()` names the missing variable rather
# than failing vaguely. Only the request itself is unproven.

The shape of the exchange, which is worth stating because it is what makes the
tool relay in `api/voice.py` correct:

1. the browser asks *us* for a session, and we POST the dealership's
   instructions and tool schemas to `/v1/realtime/client_secrets` with the real
   key. Back comes an ephemeral secret that expires in about a minute;
2. the browser POSTs an SDP offer straight to `/v1/realtime/calls` with that
   secret and talks audio to OpenAI directly. **Audio never passes through this
   server** -- proxying it would add a round trip to every syllable, and
   latency is the whole product in a phone call;
3. tool calls come back over the WebRTC data channel, and the browser relays
   them to `/api/voice/tools`, which runs the same executors as chat.

That third step is why the rules survive a channel with no server in the audio
path. A do-not-discuss vehicle is filtered inside `search_inventory`; a clash
is refused inside `book_appointment`; provenance is downgraded inside
`save_captured_fields`. Those are guarantees regardless of what the model says.

**What does not survive is the reply guard.** In chat, a draft mentioning an
unsourced price is discarded before the buyer sees it. Here the words are in
the buyer's ear before any server sees them, so the guard runs on the
transcript afterwards (`api/voice.py`) and raises a handoff. It cannot unsay a
number. That is a real difference between the channels and it is written down
rather than papered over.
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.integrations.base import NotConfigured
from app.integrations.voice.base import VoiceProvider, VoiceSession

API = "https://api.openai.com/v1/realtime/client_secrets"
TIMEOUT = 20.0

#: Where the browser sends its SDP offer. Handed to the client rather than
#: hardcoded there, so a compatible or proxied endpoint is a server-side
#: setting and not a frontend rebuild.
CALLS_URL = "https://api.openai.com/v1/realtime/calls"


def api_key() -> str:
    """VOICE_PROVIDER_KEY when set, otherwise the LLM key.

    One key is the normal case. Requiring the same secret under a second name
    means two places to rotate it and one of them being stale.
    """
    return settings.voice_provider_key or settings.openai_api_key


class OpenAIRealtimeProvider(VoiceProvider):
    name = "openai_realtime"

    def check(self) -> None:
        if not api_key():
            raise NotConfigured(
                "voice",
                ["OPENAI_API_KEY"],
                "VOICE_PROVIDER=openai needs an API key. It reuses OPENAI_API_KEY, or "
                "VOICE_PROVIDER_KEY if voice should bill to a different project.",
            )

    def session_payload(self, instructions: str, tools: list[dict]) -> dict:
        """The request body, built separately so it can be asserted without
        being sent -- the same trick `ResendSender.payload` uses, and the only
        way to check this without a key.

        `session.type` and the audio config nested under `session.audio` are
        the current shape; the older flat `voice` and `input_audio_*` keys at
        session level are rejected outright.
        """
        return {
            "session": {
                "type": "realtime",
                "model": settings.voice_model,
                "instructions": instructions,
                "audio": {
                    "input": {
                        # Without a transcription model the buyer's own words
                        # never reach us: the model hears them and answers, and
                        # the dealer's transcript is a monologue.
                        "transcription": _transcription(),
                        # Cleans the input before anything reads it. Skipping it
                        # leaves the transcriber and the turn detector both
                        # working on whatever the headset produced, and a
                        # Bluetooth mic in hands-free mode produces very little.
                        "noise_reduction": {"type": settings.voice_noise_reduction},
                        # Server-side turn taking. The browser deciding when a
                        # sentence ended is exactly the judgement a realtime
                        # model is better at.
                        "turn_detection": _turn_detection(),
                    },
                    "output": {"voice": settings.voice_voice},
                },
                # Flat function definitions -- name and parameters at the top
                # level -- which is the same shape the Responses API takes, so
                # `_openai_tools()` is reused rather than copied. A second
                # conversion is how a tool ends up offered on one channel only.
                "tools": _realtime_tools(tools),
                "tool_choice": "auto",
            }
        }

    def mint_session(self, instructions: str, tools: list[dict]) -> VoiceSession:
        self.check()
        try:
            response = httpx.post(
                API,
                json=self.session_payload(instructions, tools),
                headers={
                    "Authorization": f"Bearer {api_key()}",
                    "Content-Type": "application/json",
                },
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            # Never reached OpenAI at all -- a blocked egress, no DNS, a
            # timeout. Distinct from a rejection, and the person debugging this
            # needs to know which of the two it was.
            raise NotConfigured(
                "voice", [],
                f"Could not reach OpenAI to start the call: {exc}",
            ) from None

        if response.status_code >= 400:
            # Verbatim. OpenAI's errors name the actual problem -- a model the
            # project cannot use, a malformed session -- and summarising them
            # into "could not start the call" is how someone spends an
            # afternoon guessing.
            raise NotConfigured(
                "voice", [],
                f"OpenAI returned {response.status_code}: {response.text[:500]}",
            )

        data = response.json()
        return VoiceSession(
            provider=self.name,
            # The session id, when the response carries one. It is a handle for
            # the vendor's own logs, not something this app keys on.
            session_id=str((data.get("session") or {}).get("id") or ""),
            client_secret=data.get("value") or "",
            # Documented as about a minute. Reported rather than assumed so the
            # page can say how long the buyer has to press the button, and
            # derived from the expiry OpenAI actually returned where there is
            # one.
            expires_in=_seconds_left(data.get("expires_at")),
            model=settings.voice_model,
        )


def _transcription() -> dict:
    """The transcriber's own settings.

    The language hint is the one that matters and the one everybody omits.
    Without it the transcriber infers the language from the audio, and
    telephone-quality audio -- which is what a Bluetooth headset gives you the
    moment its microphone is opened -- is exactly where inference goes wrong.
    The result is not a slightly-off transcript, it is confident nonsense in
    the wrong language.
    """
    config: dict = {"model": settings.voice_transcribe_model}
    if settings.voice_language:
        config["language"] = settings.voice_language
    return config


def _turn_detection() -> dict:
    """When the buyer has finished speaking.

    `semantic_vad` asks whether the sentence sounded complete;
    `server_vad` times a silence. On clean audio the difference is taste. On
    poor audio it is not: gappy input reads as silence, so a fixed threshold
    cuts the buyer off mid-sentence and the model answers half a question --
    which from the buyer's side looks exactly like not being heard.

    `interrupt_response` is on either way. Talking over an assistant that
    keeps going is the single most infuriating thing a phone bot does.
    """
    if settings.voice_turn_detection == "server_vad":
        return {"type": "server_vad", "interrupt_response": True}
    return {
        "type": "semantic_vad",
        "eagerness": settings.voice_eagerness,
        "interrupt_response": True,
    }


def _realtime_tools(tools: list[dict]) -> list[dict]:
    """TOOL_DEFS is written in Anthropic's shape. Same content, realtime's."""
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }
        for t in tools
    ]


def _seconds_left(expires_at: object) -> int:
    """`expires_at` is epoch seconds. Anything else, fall back to a minute --
    the documented lifetime -- rather than to zero, which the page would read
    as already expired."""
    from time import time

    try:
        left = int(expires_at) - int(time())  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 60
    return max(left, 0)
