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
from app.integrations.email.resend import ResendSender
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
    if settings.email_sender == "resend":
        return ResendSender()
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
    from app.agent.providers import PROVIDERS, provider_key_present, provider_key_setting

    live = settings.llm_mode == "live"
    provider = settings.llm_provider.lower()
    known = provider in PROVIDERS
    has_key = provider_key_present()
    configured = live and known and has_key

    missing: list[str] = []
    if not known:
        missing.append("LLM_PROVIDER")
    elif not has_key:
        missing.append(provider_key_setting())
    if not live:
        missing.append("LLM_MODE=live")

    if configured:
        detail = f"Live {provider} tool loop on {_model_name(provider)}."
    elif not known:
        detail = (
            f"LLM_PROVIDER={settings.llm_provider!r} is not one of "
            f"{', '.join(sorted(PROVIDERS))}."
        )
    else:
        detail = (
            "Running the scripted stub agent. It calls the real tools and writes the "
            "real rows, but the wording is canned and it cannot improvise. Set "
            f"{provider_key_setting()} and LLM_MODE=live for free-form replies."
        )

    return IntegrationStatus(
        key="llm",
        label="Language model",
        configured=configured,
        impl=provider if configured else "stub",
        missing=missing,
        detail=detail,
    )


def _model_name(provider: str) -> str:
    return {"openai": settings.openai_model, "anthropic": settings.anthropic_model}.get(
        provider, "?"
    )


# What each sender needs, asked of the sender rather than hardcoded here. This
# block used to name Google's variables whatever was selected, so choosing
# Resend put a banner on screen telling the operator to set
# GOOGLE_SERVICE_ACCOUNT_JSON.
SENDER_HINTS = {
    "resend": ["EMAIL_SENDER=resend", "RESEND_API_KEY", "SENDING_DOMAIN"],
    "gmail": ["EMAIL_SENDER=gmail", "GOOGLE_SERVICE_ACCOUNT_JSON", "GMAIL_IMPERSONATE"],
}


def _email_status() -> IntegrationStatus:
    sender = get_email_sender()
    missing: list[str] = []
    detail = ""
    configured = False
    if sender.delivers:
        try:
            sender.check()
            configured = True
            detail = f"Sending as {getattr(sender, 'from_address', lambda: sender.name)()}."
        except Exception as exc:  # NotConfigured
            missing = getattr(exc, "missing", [])
            detail = getattr(exc, "detail", str(exc))
    else:
        # Nothing is selected, so there is no one sender to name. Point at the
        # one this deployment is meant to use and leave the other discoverable.
        missing = SENDER_HINTS["resend"]
        detail = "Outreach is recorded in the local outbox. No mail is delivered."
    return IntegrationStatus(
        key="email",
        label="Email delivery",
        configured=configured,
        impl=sender.name,
        missing=missing,
        detail=detail,
    )


def _inbound_status() -> IntegrationStatus:
    """Receiving is a separate thing from sending and fails separately.

    A deployment can send perfectly and silently drop every reply -- the
    Cloudflare route is configured somewhere else entirely -- so folding this
    into the email row would hide exactly the half that breaks quietly.
    """
    missing = []
    if not settings.webhook_secret:
        missing.append("WEBHOOK_SECRET")
    if not settings.sending_domain:
        missing.append("SENDING_DOMAIN")
    configured = not missing
    return IntegrationStatus(
        key="inbound_email",
        label="Email replies",
        configured=configured,
        impl="webhook" if configured else "none",
        missing=missing,
        detail=(
            f"Replies to reply+<token>@{settings.sending_domain} are accepted at "
            "/api/inbound-email. The Cloudflare route that delivers them is "
            "configured outside this app -- see integrations/email/worker/README.md."
            if configured
            else "The inbound endpoint refuses everything without a shared secret. "
            "Nothing can write to a buyer's history unauthenticated."
        ),
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
    return [
        _llm_status(), _email_status(), _inbound_status(),
        _voice_status(), _scraper_status(),
    ]


def registry_payload() -> dict:
    statuses = all_statuses()
    return {
        "integrations": [asdict(s) for s in statuses],
        "unconfigured": [s.key for s in statuses if not s.configured],
        "demo_mode": settings.demo_mode,
        # Named, counted and spelled out, because "who can we actually email?"
        # is a question the answer to which used to require reading two
        # settings and knowing which one won.
        "outbound_scope": settings.outbound_scope,
        "outbound_recipients": settings.outbound_recipients,
        "llm_mode": settings.llm_mode,
    }
