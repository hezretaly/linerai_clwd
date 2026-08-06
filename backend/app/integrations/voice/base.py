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
        missing = []
        if not settings.voice_provider:
            missing.append("VOICE_PROVIDER")
        if not settings.voice_provider_key:
            missing.append("VOICE_PROVIDER_KEY")
        return missing

    def check(self) -> None:
        raise NotConfigured(
            "voice",
            self._missing(),
            "No realtime voice vendor has been selected yet (plan §9 spike). "
            "The call UI, session mint and tool relay are built; only audio is missing.",
        )

    def mint_session(self, instructions: str, tools: list[dict]) -> VoiceSession:
        self.check()
        raise AssertionError("unreachable")
