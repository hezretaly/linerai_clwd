"""Application settings.

Every key has a working default: the app must boot, seed, and complete the full
demo flow with no ``.env`` at all. Keys that reach a real external service are
unset by default -- the corresponding integration then reports itself as
not-configured (see ``app.integrations.registry``) rather than pretending.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

DEV_SESSION_SECRET = "liner-dev-insecure-session-secret-change-me"
DEV_SEED_PASSWORD = "liner-dev"
# The inbound webhook needs a shared secret to be exercised at all, so it
# gets a development default like the passwords do -- and, like them, is
# refused in production. Leaving it empty would mean `make smoke` could only
# ever assert the 503, and the signature check, the dedupe and the whole
# resolution ladder would ship untested.
DEV_WEBHOOK_SECRET = "liner-dev-inbound-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "development"

    # --- Agent -------------------------------------------------------------
    # stub : rail-driven state machine calling the real tools (no key needed)
    # live : a real model driving the same tools, free-form
    llm_mode: str = "stub"
    # Which vendor answers when llm_mode=live. Both drive the identical tools
    # and pass the identical guards; only the wire format differs.
    llm_provider: str = "openai"
    openai_api_key: str = ""
    # Overridable, because model names move faster than this file does.
    openai_model: str = "gpt-5.4-nano"
    # gpt-5.x are reasoning models: reasoning tokens are spent from the same
    # budget as the reply, so too small a ceiling returns an empty message
    # rather than a short one.
    openai_max_output_tokens: int = 4096
    # minimal | low | medium | high. A buyer is watching a spinner, and picking
    # a car out of five search results is not a hard reasoning problem.
    openai_reasoning_effort: str = "low"
    openai_base_url: str = ""  # for an Azure or compatible endpoint
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # --- Email -------------------------------------------------------------
    # outbox  : records a real outreach row, renders in the dev outbox, sends nothing
    # console : prints to stdout (used by scripts)
    # gmail   : Google Workspace service account; written, never executed
    # resend  : the REST API this deployment is meant to use
    email_sender: str = "outbox"
    google_service_account_json: str = ""
    gmail_impersonate: str = ""
    resend_api_key: str = ""
    # The domain mail leaves from, and the one replies come back to. Both
    # halves need it: outbound builds `Reply-To: reply+<token>@here`, and the
    # Cloudflare catch-all on the same domain is what routes those back.
    sending_domain: str = ""
    sending_from: str = ""  # display address; defaults to liner@<sending_domain>
    # Shared with the Cloudflare Worker. The development default makes the
    # inbound path testable; production refuses to boot on it, because an
    # intake anyone can guess the secret for is an intake anyone can write to.
    webhook_secret: str = DEV_WEBHOOK_SECRET

    # --- Voice -------------------------------------------------------------
    voice_provider: str = ""
    voice_provider_key: str = ""

    # --- Scraper -----------------------------------------------------------
    scraper_base_url: str = ""
    scraper_user_agent: str = "LinerAI-Ingest/0.1 (+https://liner.ai/bot)"
    scraper_rate_limit: float = 1.5
    scraper_max_pages: int = 60

    # --- Core --------------------------------------------------------------
    database_url: str = f"sqlite:///{BACKEND_DIR / 'liner.db'}"
    session_secret: str = DEV_SESSION_SECRET
    # Seeded credentials, one per role. A manager sees every lead, the team page
    # and the assistant settings; a rep sees the floor. Sharing one password
    # across both would make the role distinction decorative, and on a public
    # host it would mean handing a test user the manager account. Both default
    # to the documented dev value and both are refused in production.
    manager_password: str = DEV_SEED_PASSWORD
    rep_password: str = DEV_SEED_PASSWORD
    # How this install is reached from outside, e.g. https://liner.example.com.
    # Only needed for links that leave the building: a tracked link in an email
    # has to be absolute, and it is built from the request when this is empty.
    # That is right behind the shipped nginx config (`proxy_set_header Host
    # $host`) and wrong behind a proxy that drops Host -- which would put
    # http://127.0.0.1:8000 in a buyer's inbox. Set it and the guessing stops.
    public_base_url: str = ""
    session_cookie: str = "liner_session"
    demo_mode: bool = True
    email_allowlist: str = ""
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    dealership_config: Path = BACKEND_DIR / "config" / "dealership.yaml"
    # A sample lot, loaded on top of the curated fixtures by `make seed`. One
    # copy, read from where it lives, rather than duplicated into the backend.
    inventory_csv: Path = REPO_DIR / "dash" / "cars.csv"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def allowlist(self) -> list[str]:
        return [e.strip().lower() for e in self.email_allowlist.split(",") if e.strip()]

    @property
    def is_production(self) -> bool:
        return self.env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.is_production and settings.session_secret == DEV_SESSION_SECRET:
        raise RuntimeError(
            "SESSION_SECRET is still the development default while ENV=production. "
            "Set a real secret before deploying."
        )
    stale = [
        name
        for name, value in (
            ("MANAGER_PASSWORD", settings.manager_password),
            ("REP_PASSWORD", settings.rep_password),
        )
        if value == DEV_SEED_PASSWORD
    ]
    if settings.is_production and settings.webhook_secret == DEV_WEBHOOK_SECRET:
        raise RuntimeError(
            "WEBHOOK_SECRET is still the development default while ENV=production. "
            "It is the only thing standing in front of /api/inbound-email, which "
            "writes into a buyer's history. Set a real one, and set the same value "
            "as a secret on the Cloudflare Worker."
        )
    if settings.is_production and stale:
        raise RuntimeError(
            f"{' and '.join(stale)} still set to 'liner-dev' while ENV=production. "
            "That password is printed in the README, so anyone who found the URL "
            "would have the dashboard and every lead in it. Set real ones before "
            "deploying."
        )
    if settings.is_production and settings.manager_password == settings.rep_password:
        raise RuntimeError(
            "MANAGER_PASSWORD and REP_PASSWORD are the same. Give the manager "
            "account its own password, or the role split is decorative."
        )
    return settings


settings = get_settings()
