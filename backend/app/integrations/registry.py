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
from app.integrations.voice.openai_realtime import OpenAIRealtimeProvider


@dataclass
class IntegrationStatus:
    key: str
    label: str
    configured: bool
    impl: str
    missing: list[str]
    detail: str
    #: True when the deployment switched this channel off on purpose
    #: (`CALLING=false`, `TEXTING=false`). Not configured and switched off are
    #: different facts with different fixes: the first goes in the amber
    #: banner and the boot warning, the second is a decision and goes in
    #: neither -- a banner nagging about a choice somebody made is one people
    #: learn to ignore, and then it stops working for the case it exists for.
    switched_off: bool = False


def get_email_sender() -> EmailSender:
    if settings.email_sender == "resend":
        return ResendSender()
    if settings.email_sender == "gmail":
        return GmailSender()
    if settings.email_sender == "console":
        return ConsoleSender()
    return OutboxSender()


def get_voice_provider() -> VoiceProvider:
    # One implementation, selected explicitly. Empty means voice is off rather
    # than "try OpenAI and see": a dealership that has not decided about phone
    # calls should not have one start because a key happened to be present for
    # the chat agent.
    if settings.voice_provider.lower() in {"openai", "openai_realtime"}:
        return OpenAIRealtimeProvider()
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
            # The address a dealership's mail actually leaves from. It asked
            # for a `from_address` attribute no sender has, so the line read
            # "Sending as resend" -- the vendor, where the one fact somebody
            # opens this for is which mailbox.
            detail = f"Sending as {sender.default_address('dealership') or sender.name}."
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
    from app.profile import mail_domain as _reply_domain

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
            f"Replies to reply+<token>@{_reply_domain()} are accepted at "
            "/api/inbound-email. The Cloudflare route that delivers them is "
            "configured outside this app -- see integrations/email/worker/README.md."
            if configured
            else "The inbound endpoint refuses everything without a shared secret. "
            "Nothing can write to a buyer's history unauthenticated."
        ),
    )


def _voice_status() -> IntegrationStatus:
    # The deployment's switch is reported as itself, never folded into "not
    # configured": a key that is present and a line that is switched off are
    # different facts with different fixes, and one boolean over both sends
    # whoever reads it to the wrong line of `.env`.
    if not settings.calling:
        return IntegrationStatus(
            key="voice",
            label="Voice",
            configured=False,
            impl="switched off",
            missing=["CALLING"],
            detail=(
                "Calling is switched off for this deployment (CALLING=false). No "
                "storefront shows a Call button and /call refuses to start a call. "
                "Set CALLING=true and restart to offer it again."
            ),
            switched_off=True,
        )
    provider = get_voice_provider()
    try:
        provider.check()
        return IntegrationStatus(
            key="voice",
            label="Voice",
            configured=True,
            impl=provider.name,
            missing=[],
            detail=(
                f"Calls run on {settings.voice_model}, speaking as "
                f"{settings.voice_voice}. Audio goes browser-to-OpenAI directly; tool "
                "calls come back through /api/voice/tools, so the executors that filter "
                "inventory and refuse a clash apply on a call too. The reply guard does "
                "not -- it runs on the transcript afterwards and raises a handoff, "
                "because on a call the words are already spoken."
            ),
        )
    except Exception as exc:
        return IntegrationStatus(
            key="voice",
            label="Voice",
            configured=False,
            impl="none",
            missing=getattr(exc, "missing", []),
            detail=getattr(exc, "detail", str(exc)),
        )


def _sms_status() -> IntegrationStatus:
    """Texting a buyer: the same Twilio account as the phone line, plus the
    deployment's own switch. Reported as its own row because a rep's "Text
    them" button reads it -- a button drawn for a channel that refuses every
    send is the failure `/api/integrations` exists to prevent."""
    from app.integrations import twilio_account as account

    if not settings.texting:
        return IntegrationStatus(
            key="sms",
            label="Texting",
            configured=False,
            impl="switched off",
            missing=["TEXTING"],
            detail=(
                "Texting is switched off for this deployment (TEXTING=false). The "
                "buyer page shows no Text button and a send is refused; a text that "
                "arrives is still recorded. Set TEXTING=true and restart to offer it."
            ),
            switched_off=True,
        )
    missing = account.missing()
    return IntegrationStatus(
        key="sms",
        label="Texting",
        configured=not missing,
        impl="twilio" if not missing else "none",
        missing=missing,
        detail=(
            "A rep can text a buyer from their page, from the dealership's number; "
            "replies land on the timeline. No assistant is connected to SMS."
            if not missing
            else "Texting needs the phone line's Twilio account and number."
        ),
    )


def _scraper_status() -> IntegrationStatus:
    """Where vehicles come from -- and the database is a real answer.

    This used to report `configured: false` with SCRAPER_BASE_URL missing,
    which put "Inventory source" in the amber not-configured banner. That was
    wrong in a way the banner is specifically meant to avoid: the banner exists
    to stop someone demoing on a placeholder without realising, and inventory
    is not a placeholder. The rows are real, `search_inventory` reads them, and
    they arrive by seed, by CSV import or by hand.

    Scraping is an *additional* source, and one that cannot be built
    speculatively: no two dealer sites are laid out alike, so an adapter needs
    a real URL before it means anything. An optional second source that nobody
    has chosen yet is not a broken dependency, and listing it as one trains
    whoever reads that banner to ignore it.
    """
    from app import profile

    source = profile.inventory()
    scraping = bool(source["source_url"])
    return IntegrationStatus(
        key="scraper",
        label="Inventory source",
        configured=True,
        impl="http" if scraping else "database",
        missing=[],
        detail=(
            f"Ingesting from {source['source_url']}, on top of the local database."
            if scraping
            else "Vehicles come from the local database -- seeded, imported from CSV, or "
            "edited by hand. Scraping a dealer's own site is an optional second source "
            "and needs a per-dealership adapter, so set `inventory.source_url` in the "
            "dealership's profile only once there is a real site to point at."
        ),
    )


def all_statuses() -> list[IntegrationStatus]:
    return [
        _llm_status(), _email_status(), _inbound_status(),
        _voice_status(), _sms_status(), _scraper_status(),
    ]


def registry_payload() -> dict:
    statuses = all_statuses()
    return {
        "integrations": [asdict(s) for s in statuses],
        # A channel switched off on purpose is not unconfigured: it stays out
        # of the banner and the boot warning, and says so on its own row.
        "unconfigured": [s.key for s in statuses if not s.configured and not s.switched_off],
        "demo_mode": settings.demo_mode,
        # Named, counted and spelled out, because "who can we actually email?"
        # is a question the answer to which used to require reading two
        # settings and knowing which one won.
        "outbound_scope": settings.outbound_scope,
        "outbound_recipients": settings.outbound_recipients,
        "llm_mode": settings.llm_mode,
    }
