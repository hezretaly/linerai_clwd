"""Voice provider interface.

There is deliberately no fake implementation. The plan's original
``FakeVoiceProvider`` pushed a scripted transcript on a timer; that was dropped
so nothing in the product pretends a call happened. With no provider configured
``/call`` renders a not-configured state and the session-mint endpoint returns a
typed ``not_configured`` error naming the missing key.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.integrations.base import NotConfigured


@dataclass
class VoiceSession:
    provider: str
    session_id: str
    client_secret: str
    expires_in: int
    model: str = ""


class VoiceProvider:
    name = "base"

    def check(self) -> None:
        raise NotImplementedError

    def mint_session(self, instructions: str, tools: list[dict]) -> VoiceSession:
        raise NotImplementedError


class UnconfiguredVoiceProvider(VoiceProvider):
    """Active whenever VOICE_PROVIDER is unset. Every call raises with the
    missing keys named, which is what the UI displays."""

    name = "unconfigured"

    def _missing(self) -> list[str]:
        # Only the choice. The key is the *provider's* business to ask for, and
        # naming VOICE_PROVIDER_KEY here sent people looking for a second
        # secret that the OpenAI path does not want -- it reuses OPENAI_API_KEY.
        return ["VOICE_PROVIDER=openai"]

    def check(self) -> None:
        raise NotConfigured(
            "voice",
            self._missing(),
            "Calls are switched off. Set VOICE_PROVIDER=openai to answer the phone with "
            "the OpenAI realtime model; it reuses OPENAI_API_KEY. Left empty on purpose: "
            "a key present for the chat agent should not start a dealership taking calls "
            "it has not decided to take.",
        )

    def mint_session(self, instructions: str, tools: list[dict]) -> VoiceSession:
        self.check()
        raise AssertionError("unreachable")
