"""Which implementation is live, derived from config rather than hand-maintained.

Feeds ``/api/health`` and ``/api/integrations``, which in turn feed the amber
banner in the UI. The failure mode this whole approach creates is demoing on
placeholders without realising it, so the answer has to be visible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.config import settings
from app.integrations.email.base import EmailSender
from app.integrations.email.gmail import GmailSender
from app.integrations.email.outbox import ConsoleSender, OutboxSender
from app.integrations.voice.base import UnconfiguredVoiceProvider, VoiceProvider


@dataclass
class IntegrationStatus:
    key: str
    label: str
    configured: bool
    impl: str
    missing: list[str]
    detail: str


def get_email_sender() -> EmailSender:
    if settings.email_sender == "gmail":
        return GmailSender()
    if settings.email_sender == "console":
        return ConsoleSender()
    return OutboxSender()


def get_voice_provider() -> VoiceProvider:
    # No provider has been selected yet (§9 spike is outstanding), so this is
    # the only implementation that exists.
    return UnconfiguredVoiceProvider()


def _llm_status() -> IntegrationStatus:
    live = settings.llm_mode == "live"
    has_key = bool(settings.anthropic_api_key)
    configured = live and has_key
    missing: list[str] = []
    if not has_key:
        missing.append("ANTHROPIC_API_KEY")
    if not live:
        missing.append("LLM_MODE=live")
    return IntegrationStatus(
        key="llm",
        label="Language model",
        configured=configured,
        impl="anthropic" if configured else "stub",
        missing=missing,
        detail=(
            "Live Anthropic tool loop."
            if configured
            else "Running the scripted stub agent. It calls the real tools and writes the "
            "real rows, but the wording is canned and it cannot improvise."
        ),
    )


def _email_status() -> IntegrationStatus:
    sender = get_email_sender()
    missing: list[str] = []
    detail = ""
    configured = False
    if sender.delivers:
        try:
            sender.check()
            configured = True
            detail = f"Sending as {settings.gmail_impersonate}."
        except Exception as exc:  # NotConfigured
            missing = getattr(exc, "missing", [])
            detail = getattr(exc, "detail", str(exc))
    else:
        missing = ["EMAIL_SENDER=gmail", "GOOGLE_SERVICE_ACCOUNT_JSON", "GMAIL_IMPERSONATE"]
        detail = "Outreach is recorded in the local outbox. No mail is delivered."
    return IntegrationStatus(
        key="email",
        label="Email delivery",
        configured=configured,
        impl=sender.name,
        missing=missing,
        detail=detail,
    )


def _voice_status() -> IntegrationStatus:
    provider = get_voice_provider()
    try:
        provider.check()
        return IntegrationStatus("voice", "Voice", True, provider.name, [], "")
    except Exception as exc:
        return IntegrationStatus(
            key="voice",
            label="Voice",
            configured=False,
            impl="none",
            missing=getattr(exc, "missing", []),
            detail=getattr(exc, "detail", str(exc)),
        )


def _scraper_status() -> IntegrationStatus:
    configured = bool(settings.scraper_base_url)
    return IntegrationStatus(
        key="scraper",
        label="Inventory source",
        configured=configured,
        impl="http" if configured else "none",
        missing=[] if configured else ["SCRAPER_BASE_URL"],
        detail=(
            f"Ingesting from {settings.scraper_base_url}."
            if configured
            else "No dealer site configured. Inventory can still be imported from CSV."
        ),
    )


def all_statuses() -> list[IntegrationStatus]:
    return [_llm_status(), _email_status(), _voice_status(), _scraper_status()]


def registry_payload() -> dict:
    statuses = all_statuses()
    return {
        "integrations": [asdict(s) for s in statuses],
        "unconfigured": [s.key for s in statuses if not s.configured],
        "demo_mode": settings.demo_mode,
        "llm_mode": settings.llm_mode,
    }
