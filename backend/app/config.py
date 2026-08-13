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
    # Luna is the cheapest tier of the 5.6 family -- $1/$6 per million against
    # the flagship's rates -- and it is what this assistant needs: picking a
    # car out of five search results and writing two sentences is a
    # high-volume, low-difficulty job, which is the case Luna is built for.
    openai_model: str = "gpt-5.6-luna"
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
    # `openai` is the only implementation. Empty means voice is off, and the
    # call page says so rather than failing on a button press.
    voice_provider: str = ""
    # Optional. Empty falls back to OPENAI_API_KEY, because one key is the
    # normal case and asking for the same secret twice is how the two drift.
    # Set it only to bill voice to a different project.
    voice_provider_key: str = ""
    # The alias, not a dated snapshot. A pinned snapshot is a thing that stops
    # existing without anyone touching this repository.
    voice_model: str = "gpt-realtime"
    # Who the dealership sounds like. `marin` is OpenAI's newest and best
    # sounding -- a young American woman, clear and unhurried, which is what a
    # showroom wants answering the phone. The runners-up are worth trying back
    # to back before settling, because a voice is a branding decision and
    # nothing in this repository can listen to them:
    #   marin   -- young, American, clear. The default.
    #   coral   -- warmer and chattier, a little less crisp.
    #   shimmer -- brighter and more energetic; can read as hurried.
    #   sage    -- calm and measured, reads older.
    # `alloy`, `ash`, `ballad`, `echo`, `verse` and `cedar` are the rest.
    voice_voice: str = "marin"
    # Without this the buyer's own words never arrive: the model hears audio
    # and answers, but nothing writes their side of the call into `messages`,
    # so the dealer's transcript is a monologue.
    #
    # Worth knowing when this looks wrong: transcription is a *side channel*.
    # The model itself hears the raw audio and never reads this text, so a
    # garbled transcript means the microphone is poor, not that the assistant
    # misunderstood. The two symptoms share a cause; changing this setting
    # fixes only the record.
    voice_transcribe_model: str = "gpt-4o-mini-transcribe"
    # ISO-639-1, and documented to improve accuracy *and* latency. Left unset,
    # the transcriber guesses the language from audio -- and telephone-quality
    # audio is exactly where it guesses wrong, which reads as a transcript
    # full of nonsense words. Empty means let it guess.
    voice_language: str = "en"
    # `semantic_vad` lets the model judge whether a sentence finished, rather
    # than timing a silence. It is markedly better on poor input, which is the
    # case that matters: a Bluetooth headset in hands-free mode produces gappy
    # 16 kHz audio that a fixed threshold reads as the end of every phrase, so
    # the buyer gets interrupted mid-sentence and the model answers half a
    # question. `server_vad` is the alternative and is cheaper to reason about.
    voice_turn_detection: str = "semantic_vad"
    # How fast to jump in. `low` waits longer for a buyer who is still
    # thinking; `high` feels snappier and talks over people.
    voice_eagerness: str = "medium"
    # `near_field` is a headset or a phone held to the face -- the mic is close
    # to the mouth. `far_field` is a laptop across a desk. The wrong one is
    # worse than none: far-field processing on a close mic strips the speech
    # it should be keeping.
    voice_noise_reduction: str = "near_field"

    # --- What a call costs -------------------------------------------------
    # A realtime call bills the *whole conversation so far* as input on every
    # single turn. Left alone that grows without limit, and by the tenth turn
    # most of the bill is re-reading the first nine. These three settings are
    # the only brakes there are.
    #
    # Output audio is the dearest stream on the call -- twice the price of
    # input, and the assistant generates roughly twice as many tokens per
    # second as a person speaks. A cap is a hard stop on a rambling turn; the
    # prompt asks for two sentences, and this is what happens when it does not
    # get them. Generous enough that a real answer is never cut off.
    voice_max_output_tokens: int = 1200
    # Drop the oldest turns once the conversation gets long, keeping this
    # fraction. 0.8 rather than something aggressive because each truncation
    # invalidates the prompt cache from that point -- and cached input is
    # discounted by roughly eighty times, so truncating often costs more than
    # the tokens it saves.
    voice_retention_ratio: float = 0.8
    # Transcribing the buyer's side is billed *separately* from the call. It
    # buys the dealer a readable transcript and nothing else -- the model
    # hears the audio directly and never reads it -- so it is the one part of
    # a call that can be switched off without changing what the assistant can
    # do. Off means a transcript with Liner's half only.
    voice_transcribe: bool = True

    # Dollars per million tokens, for turning recorded usage into money.
    #
    # Unset by default, and that is the point: the rates follow VOICE_MODEL
    # from the table in `integrations/voice/openai_realtime.py`, so switching
    # to the mini model does not leave this dashboard reporting the flagship's
    # prices on cheaper traffic -- a threefold overstatement that nothing else
    # on the page would contradict. Set one of these and it wins for every
    # model, which is what you want when a vendor changes a price before this
    # repository catches up.
    voice_price_audio_in: float | None = None
    voice_price_audio_out: float | None = None
    voice_price_text_in: float | None = None
    voice_price_text_out: float | None = None
    # Cached input is the whole ballgame on a long call: roughly an eightieth
    # of fresh input, so when caching stops hitting the same call costs several
    # times more with nothing else different.
    voice_price_cached_in: float | None = None

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

    # --- Who outbound email may reach ---------------------------------------
    # One setting whose name is the rule. Three ways to write it:
    #
    #   OUTBOUND_ONLY_TO=                       nobody -- every send is refused
    #   OUTBOUND_ONLY_TO=me@x.com,you@y.com     only those addresses
    #   OUTBOUND_ONLY_TO=everyone               no restriction at all
    #
    # It replaces DEMO_MODE + EMAIL_ALLOWLIST, which needed two values to say
    # one thing and read like an inbound access list. It is not: nothing here
    # has ever filtered incoming mail, and nothing does now.
    #
    # `everyone` is a word rather than an empty string on purpose. Empty
    # meaning "send to anybody" is the sort of default that goes wrong in
    # silence -- a deleted line, a mis-copied .env -- and the failure is a
    # rehearsal mailing real prospects.
    outbound_only_to: str = ""

    # Legacy pair, still read so an existing .env keeps its current behaviour.
    # Widening who can be emailed by accident is the one direction that must
    # not happen on an upgrade.
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
    def outbound_recipients(self) -> list[str] | None:
        """Addresses outbound may reach, or None for no restriction.

        None and [] are different answers and the caller must tell them apart:
        None is "send to anyone", [] is "send to nobody". Collapsing them is
        how an empty list starts meaning unrestricted.
        """
        if self.outbound_only_to.strip().lower() == "everyone":
            return None
        if self.outbound_only_to.strip():
            return [e.strip().lower() for e in self.outbound_only_to.split(",") if e.strip()]
        # Nothing set: fall back to what the old pair said, so upgrading does
        # not quietly let mail out.
        if not self.demo_mode:
            return None
        return [e.strip().lower() for e in self.email_allowlist.split(",") if e.strip()]

    @property
    def outbound_scope(self) -> str:
        """One line for the dashboard and the logs."""
        allowed = self.outbound_recipients
        if allowed is None:
            return "Outbound email can reach anyone."
        if not allowed:
            return (
                "Outbound email is refused for every address. Set OUTBOUND_ONLY_TO "
                "to a comma-separated list, or to 'everyone' to lift the limit."
            )
        return (
            f"Outbound email is limited to {len(allowed)} "
            f"address{'' if len(allowed) == 1 else 'es'}: {', '.join(allowed)}."
        )

    @property
    def allowlist(self) -> list[str]:
        """Deprecated: use outbound_recipients, which can say 'no limit'."""
        return self.outbound_recipients or []

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
