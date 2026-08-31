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

    # --- Public demo -------------------------------------------------------
    # Let anybody with the URL in, as a sales rep, with no password.
    #
    # Off by default and it must stay that way: this is the one setting in the
    # file that hands a stranger a dealership's buyer list. Everything a rep
    # can see, they can see -- names, phone numbers, email addresses, call
    # transcripts and call recordings, which are somebody's actual voice. So it
    # is only ever safe over a database of demo rows, and `make seed-demo`
    # exists to make one. Startup says loudly which it is doing.
    #
    # It is a real session for a real rep account, not a bypass: `require_manager`
    # still refuses, so the team page, settings and publishing stay shut. A
    # manager signs in with a password like always.
    public_demo: bool = False
    # Which account the door opens as. Empty picks the first active rep, which
    # is what the seed produces; naming one matters only where several exist
    # and it should be a specific person's view.
    public_demo_email: str = ""

    # --- The marketing site ------------------------------------------------
    # Where a visitor writes for help, and where they write when they want a
    # person. Two addresses on purpose: the footer offers support@, and the
    # support form answers with founder@, because somebody who has taken the
    # trouble to write in should reach a person rather than a queue.
    support_email: str = "support@linerai.us"
    founder_email: str = "founder@linerai.us"
    # When a demo can be booked into, local to the timezone named below.
    # Weekdays only; the endpoint removes anything already taken.
    demo_hours: str = "10,11,13,14,15,16"
    demo_days_ahead: int = 14
    demo_timezone: str = "US/Central"

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
    # --- Liner answering email --------------------------------------------
    #: Off, and off by default is the whole point. Taking over a mailbox is a
    #: decision a dealership makes, the same shape as `voice_provider` -- and
    #: this is the half that needs a restart, so it is the deployment's answer
    #: rather than the emergency one. The `email_agent` runtime flag is the
    #: other half, and the stricter of the two wins.
    email_agent: bool = False
    #: One reply per correspondent per this many minutes. A tuning value, so
    #: `.env` is right for it -- unlike the kill switch, which is reached for
    #: while something is going wrong and cannot wait for a restart.
    email_reply_cooldown_minutes: int = 60
    #: Across every correspondent, per hour. Per-correspondent stops one loop;
    #: a spam run across five hundred addresses walks past it, because every
    #: one is a first contact. Breaching this throws the kill switch.
    email_replies_per_hour: int = 30

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
    # A URL the buyer's browser plays on connect, before the microphone opens.
    # Empty plays a short tone instead.
    #
    # A pre-roll beats asking the model to greet: it is the same words every
    # time, it cannot be improvised into the customer's line, it cannot be
    # interrupted by the connection settling, and it costs no output audio
    # tokens -- which are the dearest thing on a call. Record the dealership's
    # own greeting in the same voice and point this at it.
    voice_greeting_audio: str = ""
    # Without this the buyer's own words never arrive: the model hears audio
    # and answers, but nothing writes their side of the call into `messages`,
    # so the dealer's transcript is a monologue.
    #
    # Worth knowing when this looks wrong: transcription is a *side channel*.
    # The model itself hears the raw audio and never reads this text, so a
    # garbled transcript means the microphone is poor, not that the assistant
    # misunderstood. The two symptoms share a cause; changing this setting
    # fixes only the record.
    # `-mini-` is the cheapest transcriber and the least accurate, and a call
    # transcript is where that shows: "E-Class" came back as 比克拉斯. This one
    # costs more per minute and is a different bill from the call itself.
    # `gpt-live-transcribe` and `gpt-transcribe` additionally accept the
    # inventory as keywords, which is the strongest lever there is on a
    # dealership's vocabulary.
    voice_transcribe_model: str = "gpt-4o-transcribe"
    # How long the transcriber may think. Higher is more accurate and slower --
    # and the latency is free here, because the model hears the audio directly
    # and never waits for this text. It only delays the dealer's record.
    voice_transcribe_delay: str = "high"
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
    # The method's signature move (section 13) is a personalized walkaround
    # video. Nothing in this system records, stores or sends one, so it is off
    # -- and the gate in the method text turns into an instruction never to
    # offer one. An assistant promising a video nobody will shoot is worse than
    # one that never mentions it.
    sales_video_enabled: bool = False
    # Transcribing the buyer's side is billed *separately* from the call. It
    # buys the dealer a readable transcript and nothing else -- the model
    # hears the audio directly and never reads it -- so it is the one part of
    # a call that can be switched off without changing what the assistant can
    # do. Off means a transcript with Liner's half only.
    voice_transcribe: bool = True
    # Transcribe the buyer's own track again once the call has ended, and keep
    # that version. The live transcriber works on a stream with no future
    # context and no second pass; this one gets the whole call as context, can
    # be retried, and is not racing anybody -- the call is over. It is a
    # separate charge from the live one and is worth having *as well*: the live
    # pass is what puts words on the rail while a rep is watching.
    voice_transcribe_after: bool = True
    # Batch transcription, not the streaming model. `whisper-1` is the only one
    # that returns segment timestamps on some accounts, and timestamps are what
    # make the merge with Liner's turns possible -- so this is its own setting
    # rather than reusing VOICE_TRANSCRIBE_MODEL, which names a *streaming*
    # model that this endpoint may not accept at all.
    voice_transcribe_after_model: str = "gpt-4o-transcribe"

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
    #: Which lot to keep when a listing page carries several. A Dealer Car
    #: Search site can show three stores' stock in one list, each card
    #: carrying its own dealer id -- and this app holds exactly one
    #: dealership, so without this Liner offers a buyer a car that is two
    #: hours from the showroom it says it is standing in. Empty keeps
    #: everything, which is right for a single-location dealer.
    scraper_dealer_id: str = ""
    #: Download each listing's photo into the dealership's own folder rather
    #: than hotlinking the dealer's image host. Off by default because it is
    #: one request per car and the default behaviour has always worked; on for
    #: a demo, where their CDN 404ing a sold car mid-presentation is a failure
    #: on the screen somebody is watching.
    scraper_save_photos: bool = False

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
    # The people who run Liner itself, rather than the dealership. They see who
    # has asked for a demo and the mail those people send -- other companies'
    # names, addresses and phone numbers -- so the same production guard
    # applies to these as to the two above.
    #
    # One per person, because a shared password is not a login: with two
    # people on one value, a leak cannot be traced and revoking it locks out
    # whoever did not leak it. `owner_password` stays as the fallback so an
    # existing `.env` written before the split keeps working -- and the boot
    # message says which key each account is actually reading.
    owner_password: str = DEV_SEED_PASSWORD
    founder_password: str = ""
    cto_password: str = ""

    def password_for_ops(self, env_key: str) -> str:
        """The password an ops account is seeded with, and where it came from.

        `env_key` is stored on the row (`ops_users.password_env`) rather than
        derived from the address, so adding a third person is a row plus one
        `.env` line and no code -- the alternative is a mapping in here that
        somebody has to remember to extend.
        """
        specific = (getattr(self, env_key.lower(), "") or "").strip()
        return specific or self.owner_password
    # How this install is reached from outside, e.g. https://liner.example.com.
    # Only needed for links that leave the building: a tracked link in an email
    # has to be absolute, and it is built from the request when this is empty.
    # That is right behind the shipped nginx config (`proxy_set_header Host
    # $host`) and wrong behind a proxy that drops Host -- which would put
    # http://127.0.0.1:8000 in a buyer's inbox. Set it and the guessing stops.
    public_base_url: str = ""
    session_cookie: str = "liner_session"
    # How many wrong passwords one account may take in a window before the
    # form starts refusing. Sized for a person who genuinely cannot remember
    # theirs -- eight tries and five minutes is several honest attempts and a
    # coffee -- while a guessing bot gets 8 tries per 5 minutes per account
    # instead of thousands per second. A correct password clears the count.
    login_max_attempts: int = 8
    login_window_seconds: int = 300

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
    #: Which prospect this instance is currently set up as. Empty keeps the
    #: single `config/dealership.yaml` an existing install already has; a name
    #: picks `config/dealerships/<name>.yaml` instead.
    #:
    #: There is still exactly one dealership in the database at a time -- no
    #: table has a dealership id and multi-tenancy is deliberately out of
    #: scope. What this buys is not running two at once but *switching*
    #: between them without losing the one you are not demoing: a profile per
    #: prospect, rather than one file edited over the top of the last.
    dealership: str = ""
    dealership_config_default: Path = BACKEND_DIR / "config" / "dealership.yaml"
    dealership_dir: Path = BACKEND_DIR / "config" / "dealerships"

    @property
    def dealership_config(self) -> Path:
        if not self.dealership.strip():
            return self.dealership_config_default
        return self.dealership_dir / f"{self.dealership.strip()}.yaml"

    # A sample lot, loaded on top of the curated fixtures by `make seed`. One
    # copy, read from where it lives, rather than duplicated into the backend.
    inventory_csv: Path = REPO_DIR / "dash" / "cars.csv"
    #: Where a profile's own `inventory.fixture_csv` is looked up. A real
    #: dealership's exported lot, committed so `make reset-db` rebuilds it with
    #: no network -- which is the answer when their site refuses the crawler.
    fixtures_dir: Path = BACKEND_DIR / "fixtures"
    #: Where a crawl's own record lives: one folder per dealership, holding
    #: `snapshot.json` and the photos. Under `var/` with the call recordings,
    #: for the same reason -- these are files a deployment accumulates, not
    #: source, and a database growing by megabytes a row ruins every backup.
    inventory_dir: Path = BACKEND_DIR / "var" / "inventory"

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
            # Each ops account resolves to its own key when one is set, so
            # the guard names the variable somebody actually has to change.
            ("FOUNDER_PASSWORD", settings.password_for_ops("founder_password")),
            ("CTO_PASSWORD", settings.password_for_ops("cto_password")),
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
        # Named individually, and OWNER_PASSWORD gets its own sentence: it
        # arrived after the first deployments did, so the way this is met in
        # practice is an install that booted yesterday refusing to boot today
        # on a variable whose .env predates it. A message that reads as "you
        # misconfigured this" sends somebody hunting for what they broke.
        raise RuntimeError(
            f"{' and '.join(stale)} still set to 'liner-dev' while ENV=production. "
            "That password is printed in the README, so anyone who found the URL "
            "would have the dashboard and every lead in it. Set real ones before "
            "deploying."
            + (
                "\n\nThese are newer than MANAGER_PASSWORD and REP_PASSWORD. If "
                "this install was running before an upgrade, its .env has no line "
                "for them and the defaults are what stopped the boot. Add them and "
                "restart:\n"
                "    printf 'FOUNDER_PASSWORD=%s\\nCTO_PASSWORD=%s\\n' "
                "\"$(openssl rand -base64 12)\" \"$(openssl rand -base64 12)\" | "
                "sudo tee -a /srv/liner/.env\n"
                "Then `make add-owners`, which puts founder@ and cto@ into "
                "ops_users on an existing database without the reseed that would "
                "take the leads with it."
                if {"FOUNDER_PASSWORD", "CTO_PASSWORD"} & set(stale) else ""
            )
        )
    # Every pair, not just the first two. `owner` reaches /ops and a dealership
    # never should, so sharing a password with the rep account is the one
    # collision that hands our own inbox to whoever works the floor.
    if settings.is_production:
        for first, second in (
            ("MANAGER_PASSWORD", "REP_PASSWORD"),
            ("MANAGER_PASSWORD", "OWNER_PASSWORD"),
            ("REP_PASSWORD", "OWNER_PASSWORD"),
        ):
            values = {
                "MANAGER_PASSWORD": settings.manager_password,
                "REP_PASSWORD": settings.rep_password,
                "OWNER_PASSWORD": settings.owner_password,
            }
            if values[first] == values[second]:
                raise RuntimeError(
                    f"{first} and {second} are the same. Give each account its "
                    "own password, or the role split is decorative."
                )
    return settings


settings = get_settings()
