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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "development"

    # --- Agent -------------------------------------------------------------
    # stub  : rail-driven state machine calling the real tools (no key needed)
    # live  : the Anthropic tool loop
    llm_mode: str = "stub"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # --- Email -------------------------------------------------------------
    # outbox  : records a real outreach row, renders in the dev outbox, sends nothing
    # console : prints to stdout (used by scripts)
    # gmail   : the real thing; requires the Google credentials below
    email_sender: str = "outbox"
    google_service_account_json: str = ""
    gmail_impersonate: str = ""

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
    session_cookie: str = "liner_session"
    demo_mode: bool = True
    email_allowlist: str = ""
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    dealership_config: Path = BACKEND_DIR / "config" / "dealership.yaml"

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
    return settings


settings = get_settings()
