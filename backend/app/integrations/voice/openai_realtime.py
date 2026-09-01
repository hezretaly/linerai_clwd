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

    def session_payload(
        self, instructions: str, tools: list[dict], keywords: list[str] | None = None
    ) -> dict:
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
                        **({"transcription": _transcription(keywords)}
                           if settings.voice_transcribe else {}),
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
                # Output audio is the dearest stream on the call and the
                # assistant generates it about twice as fast as a person
                # speaks. The prompt asks for two sentences; this is what
                # happens when it does not get them.
                "max_output_tokens": settings.voice_max_output_tokens,
                # Every turn bills the whole conversation so far as input, so
                # without this a ten-minute call spends most of its money
                # re-reading itself. The ratio is deliberately gentle: each
                # truncation invalidates the prompt cache from that point, and
                # cached input is discounted by roughly eighty times, so
                # truncating often costs more than the tokens it saves.
                "truncation": {
                    "type": "retention_ratio",
                    "retention_ratio": settings.voice_retention_ratio,
                },
            }
        }

    def mint_session(
        self, instructions: str, tools: list[dict], keywords: list[str] | None = None
    ) -> VoiceSession:
        self.check()
        try:
            response = httpx.post(
                API,
                json=self.session_payload(instructions, tools, keywords),
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


# Dollars per million tokens, by model. Only models whose published audio
# rates have been checked appear here; anything else is reported as unpriced
# rather than quietly charged at the flagship's rates, because a cost report
# that is confidently wrong is worse than one that admits it does not know.
#
# Text rates are derived at the same ratio as audio for the mini tier, which is
# the one number here that is inferred rather than published -- it moves the
# total by very little, since on a call of any length audio dominates.
VOICE_RATES: dict[str, dict[str, float]] = {
    "gpt-realtime": {
        "audio_in": 32.0, "audio_out": 64.0,
        "text_in": 4.0, "text_out": 16.0, "cached_in": 0.4,
    },
    "gpt-realtime-mini": {
        "audio_in": 10.0, "audio_out": 20.0,
        "text_in": 1.25, "text_out": 5.0, "cached_in": 0.125,
    },
}


def rates_for(model: str) -> tuple[dict[str, float], bool]:
    """The per-million rates for a model, and whether they were actually known.

    A dated snapshot is priced as its family -- `gpt-realtime-2.1` is a
    `gpt-realtime` -- but only where the suffix is a *version*, not another
    tier. A plain prefix match makes a future `gpt-realtime-nano` a flagship
    and charges it at three times its cost, silently, which is precisely the
    confident wrong answer this table exists to avoid. So the remainder has to
    begin with a digit.
    """
    name = (model or "").strip()
    known = VOICE_RATES.get(name)
    if known is None:
        for family, rate in sorted(VOICE_RATES.items(), key=lambda kv: -len(kv[0])):
            rest = name[len(family):]
            if name.startswith(family) and rest.startswith("-") and rest[1:2].isdigit():
                known = rate
                break

    # An explicit VOICE_PRICE_* always wins, for every model. A vendor can
    # change a price faster than this file can be edited.
    override = {
        "audio_in": settings.voice_price_audio_in,
        "audio_out": settings.voice_price_audio_out,
        "text_in": settings.voice_price_text_in,
        "text_out": settings.voice_price_text_out,
        "cached_in": settings.voice_price_cached_in,
    }
    base = dict(known or dict.fromkeys(override, 0.0))
    base.update({k: v for k, v in override.items() if v is not None})
    return base, known is not None or any(v is not None for v in override.values())


def price_of(usage: dict, model: str = "") -> float:
    """What one response cost, in dollars.

    An estimate, and labelled one everywhere it is shown: the authority is
    OpenAI's own billing page. What it is *not* is a guess -- the token counts
    are the ones the provider reported for that exact response.

    Priced against the model that billed it rather than the one configured
    now, so switching to mini tomorrow does not silently re-price yesterday's
    calls at the new rate and make a change look like a saving it was not.
    """
    rate, _ = rates_for(model or settings.voice_model)
    per_million = (
        usage.get("cached_tokens", 0) * rate["cached_in"]
        + max(usage.get("input_audio_tokens", 0), 0) * rate["audio_in"]
        + max(usage.get("input_text_tokens", 0), 0) * rate["text_in"]
        + usage.get("output_audio_tokens", 0) * rate["audio_out"]
        + usage.get("output_text_tokens", 0) * rate["text_out"]
    )
    return per_million / 1_000_000


#: Which transcribers accept the newer config surface -- `keywords` and
#: `delay`. Sending either to a model that does not is a 400 that takes the
#: whole call with it, so this is checked rather than hoped.
#:
#: **`delay` was not checked, and the default configuration was the broken
#: one.** `VOICE_TRANSCRIBE_MODEL` defaults to `gpt-4o-transcribe` and
#: `VOICE_TRANSCRIBE_DELAY` defaults to `high`, so a deployment that set
#: `VOICE_PROVIDER=openai` and changed nothing else got
#: `"The 'delay' parameter is not supported for this model"` and no call at
#: all. `keywords` had this guard because somebody hit it once; `delay` was
#: added a line away without one, which is the argument for the two sharing a
#: list rather than each remembering separately.
EXTENDED_MODELS = ("gpt-transcribe", "gpt-live-transcribe")

#: Kept as a name because the reasoning above is about keywords first.
KEYWORD_MODELS = EXTENDED_MODELS


def _transcription(keywords: list[str] | None = None) -> dict:
    """The transcriber's own settings.

    Three levers, and every one of them was left at its default while a real
    call turned "E-Class" into 比克拉斯:

    * **language** -- without it the transcriber infers the language from the
      audio, and telephone-quality audio is exactly where inference goes wrong.
      The result is not a slightly-off transcript, it is confident nonsense in
      another script.
    * **delay** -- more thinking time is more accuracy, and here the latency is
      *free*: the model hears the raw audio and never reads this text, so a
      slower transcriber delays only the dealer's record, never the reply.
    * **keywords** -- the dealership's own vocabulary. "E-Class", "AMG", "GLE",
      "Sienna" are precisely the words a general transcriber has no reason to
      expect and every reason to mangle, and they are sitting in the inventory
      table. Only two models accept them.
    """
    model = settings.voice_transcribe_model
    extended = model.startswith(EXTENDED_MODELS)

    config: dict = {"model": model}
    # `language` is accepted everywhere, and it is the one that matters most:
    # without it a telephone-quality "mm" comes back as 嗯.
    if settings.voice_language:
        config["language"] = settings.voice_language
    # Both of these belong to the newer transcribers only. Withheld rather
    # than sent hopefully: losing a little accuracy is a smaller cost than a
    # 400 that ends the call before it starts.
    if extended and settings.voice_transcribe_delay:
        config["delay"] = settings.voice_transcribe_delay
    if extended and keywords:
        config["keywords"] = keywords[:100]
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
