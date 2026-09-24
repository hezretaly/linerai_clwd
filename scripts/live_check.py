#!/usr/bin/env python3
"""The running system, tested against the real services, from the box itself.

    make live-check                              # production: everything
    make live-check INBOX=you@example.com        # ...plus one email to your inbox per realm
    make live-check ARGS=--plan                  # what would run, touching nothing
    make live-check ARGS=--demo                  # the demo instance: the model, and no mail out
    make live-check ARGS="--only chat,voice"     # a subset: chat voice resend inbox intake mail widget

`make smoke` proves the code with every vendor faked; this proves the
*deployment*: this box's `.env`, its database, its nginx, Cloudflare in front
of it, and the three vendors behind it. Each line is PASS, FAIL or SKIP, and a
FAIL says which layer to look at. Nothing here prints a secret -- a key is
"set" or "NOT SET", never a value or a length.

What it does, in order:

- **config** -- the settings this box runs on, and the running server's own
  view of them (a `.env` edited without a restart reads differently here).
- **chat** -- one real turn in the store's website chat, over HTTP through the
  public address, then the conversation is removed.
- **voice** -- mints a browser call session the way `/call` does (the key and
  the session body, accepted by OpenAI), then one exchange over the Realtime
  WebSocket in text, then the conversation is removed. No audio.
- **resend** -- asks Resend whether the sending domain and the store's own mail
  domain are verified.
- **inbox** -- only with `INBOX=`: one email from the dealership's address and
  one from Liner's, to you, through `OUTBOUND_ONLY_TO` like any other send.
- **intake** -- the app half of a reply: a reply token is minted in the
  store and a message is posted straight to the Worker's intake URL with the
  Worker's secret, so nginx, Cloudflare's proxy and the app are tested with
  no mail involved. Runs before the cut-over (`--intake` points it at
  127.0.0.1 to leave Cloudflare out too).
- **mail** -- the whole route: a real email through Resend to
  `reply+<token>@<store mail domain>`, back through Cloudflare Email Routing
  and the Worker, and one to Liner's `support@`. Needs `linerai.us` pointing
  at this box, because that is where the Worker posts.
  Every leg waits for the receipt, then everything it created is removed, the
  raw message on disk included. These go only to our own addresses, which is
  why they do not ask `OUTBOUND_ONLY_TO`; `is_our_address` is asserted first.
  They carry `Auto-Submitted`, so no assistant ever answers one.
- **widget** -- the website chat's loader, its settings as the dealer's own
  site asks for them, and the frame's `frame-ancestors`.

`--demo` is the other instance: it expects the outbox (nothing leaves) and
runs no round trips. `--keep` leaves what the checks created, for looking at.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
import uuid
from urllib.parse import urlsplit
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import (  # noqa: E402
    DEV_SEED_PASSWORD,
    DEV_SESSION_SECRET,
    DEV_WEBHOOK_SECRET,
    settings,
)

SECTIONS = ("chat", "voice", "resend", "inbox", "intake", "mail", "widget")
WRANGLER = ROOT / "backend/app/integrations/email/worker/wrangler.jsonc"
QUESTION = "What do you have under $25,000?"
SPOKEN = "Hi, are you open on Sunday?"


# ---------------------------------------------------------------- reporting


class Report:
    def __init__(self) -> None:
        self.counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}

    def line(self, verdict: str, name: str, detail: str = "") -> bool:
        self.counts[verdict] += 1
        tail = f"  -- {detail}" if detail else ""
        print(f"  {verdict}  {name}{tail}", flush=True)
        return verdict == "PASS"

    def ok(self, name: str, detail: str = "") -> bool:
        return self.line("PASS", name, detail)

    def fail(self, name: str, detail: str = "") -> bool:
        return self.line("FAIL", name, detail)

    def skip(self, name: str, detail: str = "") -> bool:
        return self.line("SKIP", name, detail)

    def check(self, cond: bool, name: str, good: str = "", bad: str = "") -> bool:
        return self.ok(name, good) if cond else self.fail(name, bad)


def section(title: str) -> None:
    print(f"\n== {title} ==", flush=True)


def said(value: str) -> str:
    """Whether a secret is set, and nothing else about it."""
    return "set" if (value or "").strip() else "NOT SET"


# ------------------------------------------------------------------ helpers


def seeded_stores() -> list[str]:
    from app.db import has_database
    from app.stores import known_stores

    return [slug for slug in known_stores() if has_database(slug)]


def pick_store(asked: str) -> str:
    if asked:
        return asked
    if settings.dealership.strip():
        return settings.dealership.strip()
    found = seeded_stores()
    if len(found) == 1:
        return found[0]
    raise SystemExit(
        "\nWhich store? This box has "
        + (f"{len(found)} seeded: {', '.join(found)}" if found else "none seeded")
        + ". Name it: make live-check ARGS=\"--store <slug>\"\n"
    )


def base_for(store: str, asked: str) -> str:
    """Where the store is reached from outside -- the address a buyer uses."""
    if asked:
        return asked.rstrip("/")
    from app.stores import public_link

    return (public_link("", store) or f"http://127.0.0.1:8000/{store}").rstrip("/")


def worker_raw_url() -> str:
    """Where this checkout's Worker posts a message, read from wrangler.jsonc."""
    if not WRANGLER.is_file():
        return ""
    for line in WRANGLER.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        if '"WEBHOOK_RAW_URL"' in stripped:
            return stripped.split(":", 1)[1].strip().strip('",')
    return ""


def purge(db, table: str, ids: set[str]) -> dict[str, int]:  # noqa: ANN001
    """Remove rows and everything that points at them, by the foreign keys.

    Worked out from the schema rather than listed by hand, because what a
    chat turn or a delivery writes grows with the product -- a hand-written
    list is the one that forgets the table added next month and leaves a
    live check's lead on a dealership's board. Nullable references into the
    set are cleared first, so the delete order cannot trip over a cycle.
    """
    from sqlalchemy import delete, select, update

    from app.db import Base
    import app.models  # noqa: F401  -- registers every table

    tables = list(Base.metadata.sorted_tables)
    doomed: dict[str, set] = {table: set(ids)}
    grew = True
    while grew:
        grew = False
        for t in tables:
            pk = list(t.primary_key.columns)
            if len(pk) != 1:
                continue
            for fk in t.foreign_keys:
                target = fk.column.table.name
                wanted = doomed.get(target)
                if not wanted:
                    continue
                found = set(db.execute(select(pk[0]).where(fk.parent.in_(wanted))).scalars())
                fresh = found - doomed.setdefault(t.name, set())
                if fresh:
                    doomed[t.name] |= fresh
                    grew = True
    for t in tables:
        pk = list(t.primary_key.columns)
        mine = doomed.get(t.name)
        if not mine or len(pk) != 1:
            continue
        for fk in t.foreign_keys:
            if fk.parent.nullable and doomed.get(fk.column.table.name):
                db.execute(update(t).where(pk[0].in_(mine)).values({fk.parent.name: None}))
    for t in reversed(tables):
        pk = list(t.primary_key.columns)
        mine = doomed.get(t.name)
        if mine and len(pk) == 1:
            db.execute(delete(t).where(pk[0].in_(mine)))
    db.commit()
    return {name: len(rows) for name, rows in doomed.items() if rows}


def raw_files_for(db, receipt_ids: set[str]) -> list[pathlib.Path]:  # noqa: ANN001
    """The `.eml` files the intake kept for these receipts."""
    from app.api.inbound_email import raw_path
    from app.models import EmailEnvelope

    found = []
    for env in db.query(EmailEnvelope).filter(EmailEnvelope.receipt_id.in_(receipt_ids)).all():
        path = raw_path(env.raw_path or "")
        if path is not None:
            found.append(path)
    return found


def sse_events(response) -> list[tuple[str, dict]]:  # noqa: ANN001
    events, kind = [], ""
    for line in response.iter_lines():
        if line.startswith("event: "):
            kind = line[7:].strip()
        elif line.startswith("data: "):
            try:
                events.append((kind, json.loads(line[6:])))
            except ValueError:
                events.append((kind, {"raw": line[6:]}))
    return events


# ------------------------------------------------------------------- config


def check_config(r: Report, store: str, base: str, demo: bool) -> dict:
    from app import mailboxes, profile
    from app.integrations.registry import get_email_sender

    section("config")
    print(f"  store        {store}")
    print(f"  address      {base}")
    r.check(settings.env == "production", "ENV=production", "", f"ENV={settings.env}")
    r.check(settings.session_secret != DEV_SESSION_SECRET, "SESSION_SECRET is not the development default")
    r.check(bool(settings.webhook_secret) and settings.webhook_secret != DEV_WEBHOOK_SECRET,
            "WEBHOOK_SECRET is set, and not the development default", "",
            "empty or the development default -- it has to equal the Worker's secret")
    r.check(settings.manager_password != DEV_SEED_PASSWORD, "MANAGER_PASSWORD is not the development default")
    live = settings.llm_mode == "live"
    r.check(live, "LLM_MODE=live", settings.llm_provider,
            f"LLM_MODE={settings.llm_mode}: the chat answers from the scripted stub")
    r.check(bool(settings.openai_api_key), "OPENAI_API_KEY", said(settings.openai_api_key),
            "NOT SET -- the model, the call and the drafts all need it")
    print(f"  model        {settings.openai_model}  (voice: {settings.voice_model}, "
          f"VOICE_PROVIDER={settings.voice_provider or 'empty -- calls are off'})")
    dbs = settings.database_url_template or settings.database_url
    print(f"  databases    {'Postgres' if dbs.startswith('postgres') else 'SQLite'}"
          f"{' -- one per store' if settings.database_url_template else ''}")
    print(f"  outbound     OUTBOUND_ONLY_TO: {settings.outbound_scope}")

    with mailboxes.using(store):
        seeded = store in seeded_stores()
        mail_from, mail_domain = profile.mailbox_address(), profile.mail_domain()
        embed = profile.embed_origins()
    r.check(seeded, f"{store} has a database with a dealership in it", "",
            f"not seeded -- DEALERSHIP={store} make reset-db")

    sender = get_email_sender()
    print(f"  email        EMAIL_SENDER={settings.email_sender} "
          f"({'delivers' if sender.delivers else 'records only, nothing leaves'})")
    if demo:
        r.check(not sender.delivers, "the demo sends no mail", "the outbox records it and nothing leaves",
                f"EMAIL_SENDER={settings.email_sender} delivers -- a demo must not mail anyone")
        r.check(not settings.store_domain, "the demo is served by path, not by subdomain", "",
                f"STORE_DOMAIN={settings.store_domain} -- that is production's")
    else:
        r.check(sender.delivers, "EMAIL_SENDER delivers", settings.email_sender,
                f"EMAIL_SENDER={settings.email_sender}: nothing leaves this box")
        if settings.email_sender == "resend":
            r.check(bool(settings.resend_api_key), "RESEND_API_KEY", said(settings.resend_api_key))
        r.check(bool(settings.sending_domain), "SENDING_DOMAIN", settings.sending_domain or "",
                "NOT SET -- nothing to send from and no reply address")
        r.check(bool(mail_from), f"{store} mails from its own address", mail_from,
                "no mailbox -- the profile's mailbox: and SENDING_DOMAIN")
        r.check(bool(settings.store_domain), "STORE_DOMAIN (the subdomain is the store)",
                settings.store_domain, "NOT SET -- stores are reached by path only")
    print(f"  replies to   reply+<token>@{mail_domain or '(none)'}")
    print(f"  embed        {', '.join(embed) or '(no websites listed)'}")

    # The running process, which is not necessarily what .env says now.
    import httpx

    try:
        health = httpx.get(f"{base}/api/health", timeout=15).json()
        r.check(health.get("status") == "ok", "the server answers at its public address",
                f"{base}/api/health", json.dumps(health.get("database"))[:120])
        same = health.get("llm_mode") == settings.llm_mode and health.get("env") == settings.env
        r.check(same, "and runs on the .env this check reads", "",
                f"server says env={health.get('env')} llm_mode={health.get('llm_mode')}: "
                "restart the service after editing .env")
    except Exception as exc:  # noqa: BLE001
        r.fail("the server answers at its public address",
               f"{base}/api/health: {type(exc).__name__}: {str(exc)[:160]}")
        try:
            local = httpx.get("http://127.0.0.1:8000/api/health", timeout=5).status_code
            print(f"         127.0.0.1:8000 answers {local}: the app is up, so nginx or "
                  "Cloudflare (DNS, the proxy, the certificate) is between it and the address")
        except Exception:  # noqa: BLE001
            print("         127.0.0.1:8000 does not answer either: the service is down -- "
                  "journalctl -u liner -n 50")
    return {"mail_from": mail_from, "mail_domain": mail_domain, "embed": embed,
            "sender": sender, "live": live}


# --------------------------------------------------------------------- chat


def check_chat(r: Report, store: str, base: str, keep: bool) -> None:
    import httpx

    from app import mailboxes
    from app.db import SessionLocal

    section("chat: one real turn on the website chat")
    convo_id = ""
    try:
        started = httpx.post(f"{base}/api/chat/sessions", timeout=20)
        if started.status_code != 200:
            r.fail("a chat session opens", f"{started.status_code}: {started.text[:200]}")
            return
        convo_id = started.json()["conversation_id"]
        r.ok("a chat session opens", started.json().get("dealership", {}).get("name", ""))
        t0 = time.monotonic()
        with httpx.stream("POST", f"{base}/api/chat/sessions/{convo_id}/messages",
                          json={"content": QUESTION}, timeout=120) as resp:
            if resp.status_code != 200:
                r.fail("the assistant answers", f"{resp.status_code}: {resp.read()[:200]!r}")
                return
            events = sse_events(resp)
        took = time.monotonic() - t0
        kinds = [k for k, _ in events]
        error = next((d for k, d in events if k == "error"), None)
        reply = next((d.get("content", "") for k, d in events if k == "assistant_message"), "")
        if error:
            r.fail("the assistant answers", json.dumps(error)[:300])
            return
        r.check(bool(reply), "the assistant answers", f"{took:.1f}s: {reply[:140]!r}",
                f"no reply in the stream: {kinds}")
        cars = next((d.get("vehicles", []) for k, d in events if k == "vehicles"), [])
        r.check(bool(cars), "and searched the lot for it", f"{len(cars)} car(s) on screen",
                "no cars came back -- is the inventory seeded, and under $25k?")
    except Exception as exc:  # noqa: BLE001
        r.fail("the chat", f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        if convo_id and not keep:
            with mailboxes.using(store), SessionLocal(store) as db:
                gone = purge(db, "conversations", {convo_id})
            print(f"         removed: {gone}")


# -------------------------------------------------------------------- voice


async def realtime_exchange(instructions: str, tools: list[dict], keywords: list[str]) -> dict:
    """One text turn over the Realtime socket, as the phone bridge opens it."""
    import websockets

    from app.integrations.voice.openai_realtime import (
        SOCKET_URL,
        OpenAIRealtimeProvider,
        api_key,
    )

    session = OpenAIRealtimeProvider().session_payload(instructions, tools, keywords)["session"]
    url = f"{SOCKET_URL}?model={settings.voice_model}"
    out: dict = {"updated": False, "error": "", "said": "", "tool": "", "status": "",
                 "usage": {}, "text_only": True}
    async with websockets.connect(
        url, additional_headers={"Authorization": f"Bearer {api_key()}"}, open_timeout=20
    ) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": session}))
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": SPOKEN}],
        }}))
        # Text back rather than audio: the cheapest answer that still proves
        # the session, the prompt and the tools were accepted. If this build
        # of the API does not take the field, ask again plainly.
        await ws.send(json.dumps({"type": "response.create",
                                  "response": {"output_modalities": ["text"]}}))
        retried = False
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.monotonic()))
            event = json.loads(raw)
            kind = event.get("type", "")
            if kind == "session.updated":
                out["updated"] = True
            elif kind == "error":
                message = json.dumps(event.get("error", event))
                if "output_modalities" in message and not retried:
                    retried, out["text_only"] = True, False
                    await ws.send(json.dumps({"type": "response.create"}))
                    continue
                out["error"] = message[:500]
                return out
            elif kind in ("response.output_text.delta", "response.output_audio_transcript.delta"):
                out["said"] += event.get("delta", "")
            elif kind == "response.done":
                response = event.get("response") or {}
                out["status"] = response.get("status", "")
                out["usage"] = response.get("usage") or {}
                for item in response.get("output") or []:
                    if item.get("type") == "function_call":
                        out["tool"] = item.get("name", "")
                    for part in item.get("content") or []:
                        if not out["said"]:
                            out["said"] = part.get("text") or part.get("transcript") or ""
                if response.get("status_details") and out["status"] != "completed":
                    out["error"] = json.dumps(response["status_details"])[:500]
                return out
    return out


def check_voice(r: Report, store: str, base: str, keep: bool) -> None:
    import httpx

    from app import mailboxes
    from app.db import SessionLocal

    section("voice: a browser call session, and one exchange over the Realtime socket")
    if not settings.voice_provider:
        r.skip("voice", "VOICE_PROVIDER is empty, so calls are off on this box")
        return
    if not settings.calling:
        r.skip("voice", "CALLING=false in .env")
        return
    convo_id = ""
    try:
        minted = httpx.post(f"{base}/api/voice/sessions", timeout=30)
        body = minted.json() if minted.headers.get("content-type", "").startswith("application/json") else {}
        convo_id = body.get("conversation_id", "")
        r.check(minted.status_code == 200 and bool(body.get("client_secret")),
                "OpenAI accepts the call session /call asks for",
                f"model {body.get('model')}, {body.get('expires_in')}s to connect",
                f"{minted.status_code}: {json.dumps(body)[:400] if body else minted.text[:300]}")
    except Exception as exc:  # noqa: BLE001
        r.fail("OpenAI accepts the call session /call asks for", f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        if convo_id and not keep:
            with mailboxes.using(store), SessionLocal(store) as db:
                purge(db, "conversations", {convo_id})

    try:
        from app.agent import tools
        from app.agent.prompts import build_system_prompt
        from app.api.settings import live_settings
        from app.api.voice import _spoken_words
        from app.models import Dealership

        with mailboxes.using(store), SessionLocal(store) as db:
            dealership = db.query(Dealership).first()
            instructions = build_system_prompt(db, dealership, live_settings(db), channel="voice")
            words = _spoken_words(db)
            got = asyncio.run(realtime_exchange(instructions, tools.TOOL_DEFS, words))
    except Exception as exc:  # noqa: BLE001
        r.fail("the Realtime socket answers", f"{type(exc).__name__}: {str(exc)[:300]}")
        return
    if got["error"]:
        r.fail("the Realtime socket answers", got["error"])
        return
    r.check(got["updated"], "the session is accepted over the socket", "session.updated",
            "no session.updated -- the phone bridge sends this same body")
    answered = got["status"] == "completed" and (got["said"] or got["tool"])
    usage = got["usage"]
    r.check(bool(answered), "and the model answers",
            (f"called {got['tool']}" if got["tool"] and not got["said"] else repr(got["said"][:140]))
            + f" ({usage.get('total_tokens', '?')} tokens"
            + ("" if got["text_only"] else ", audio -- this API took no text-only request") + ")",
            f"status {got['status'] or 'none'}")


# ------------------------------------------------------------------- resend


def check_resend(r: Report, store: str, info: dict) -> None:
    import httpx

    section("resend: are the domains verified")
    if settings.email_sender != "resend":
        r.skip("resend", f"EMAIL_SENDER={settings.email_sender}")
        return
    wanted = [d for d in dict.fromkeys([settings.sending_domain, info["mail_domain"]]) if d]
    try:
        resp = httpx.get("https://api.resend.com/domains", timeout=20,
                         headers={"Authorization": f"Bearer {settings.resend_api_key}"})
    except Exception as exc:  # noqa: BLE001
        r.fail("Resend answers", f"{type(exc).__name__}: {str(exc)[:200]}")
        return
    if resp.status_code in (401, 403):
        r.skip("the domain list", f"{resp.status_code}: this key is send-only, so it cannot "
               "list domains -- the sends below are the test")
        return
    if resp.status_code != 200:
        r.fail("Resend answers", f"{resp.status_code}: {resp.text[:300]}")
        return
    known = {d.get("name", "").lower(): d for d in resp.json().get("data", [])}
    for domain in wanted:
        row = known.get(domain)
        if row is None:
            r.fail(f"{domain} is in Resend", "not added -- Resend: Domains -> Add domain, "
                   "then its DNS records in Cloudflare")
            continue
        r.check(row.get("status") == "verified", f"{domain} is verified",
                f"region {row.get('region', '?')}", f"status {row.get('status')}: its DNS records "
                "are not all in place yet")


# ------------------------------------------------------------ mail: inbox


def check_inbox(r: Report, store: str, inbox: str, info: dict) -> None:
    import httpx

    from app import mailboxes, outreach_send
    from app.db import SessionLocal
    from app.integrations.email.base import with_name

    section(f"inbox: one email to {inbox} from each realm")
    sender = info["sender"]
    if not sender.delivers:
        r.skip("inbox", f"EMAIL_SENDER={settings.email_sender} delivers nothing")
        return
    stamp = time.strftime("%H:%M:%S")
    with mailboxes.using(store), SessionLocal(store) as db:
        dealer_from = outreach_send.dealership_from(db, sender)
    ops_from = sender.from_header(with_name(outreach_send.OPS_SENDER_NAME, sender.default_address("ops")))
    for realm, from_header in (("dealership", dealer_from), ("Liner", ops_from)):
        blocked = outreach_send.blocked_reason(sender, inbox)
        if blocked:
            r.fail(f"mail from {realm}", blocked)
            continue
        # Inside the store: whether the dealership's own address may go in
        # the From is decided against *this* store's mailbox, and outside it
        # the sender would quietly fall back to the deployment's `sales@`.
        where = settings.store_domain or settings.public_base_url or "the server"
        with mailboxes.using(store):
            result = sender.send(
                inbox,
                f"Liner live check {stamp}: from {realm}",
                f"This is `make live-check` on {where}, sending as {from_header}.\n\n"
                "If it is in your inbox rather than spam, that address is set up. "
                "Nothing to do.",
                from_address=from_header,
                headers={"Auto-Submitted": "auto-generated"},
            )
        if result.status != "sent":
            r.fail(f"mail from {realm}", f"{from_header}: {result.detail[:300]}")
            continue
        r.ok(f"mail from {realm} accepted by {result.provider}", from_header)
        # What happened next, where the key may ask. Best effort: a send-only
        # key is refused here, and that is not a failed send.
        if sender.name == "resend" and result.message_id:
            last = ""
            for _ in range(8):
                time.sleep(2.5)
                try:
                    seen = httpx.get(f"https://api.resend.com/emails/{result.message_id}", timeout=10,
                                     headers={"Authorization": f"Bearer {settings.resend_api_key}"})
                except Exception:  # noqa: BLE001
                    break
                if seen.status_code != 200:
                    break
                last = seen.json().get("last_event", "")
                if last in ("delivered", "bounced", "complained"):
                    break
            if last:
                r.check(last != "bounced", f"  and Resend reports it {last}")


# ------------------------------------------------------- mail: round trips


def raw_message(from_header: str, to: str, subject: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = from_header
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="live-check.invalid")
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content("A round trip from `make live-check`. It is removed once it has arrived.")
    return msg.as_bytes()


def wait_for_receipt(slugs: list[str], match, timeout: float):  # noqa: ANN001
    """Poll these stores for a filed receipt the `match` clause picks out;
    (slug, row) or None. `received` is a delivery still being filed, so it is
    waited past rather than reported."""
    from app import mailboxes
    from app.db import SessionLocal
    from app.models import InboundEmail

    deadline = time.monotonic() + timeout
    waited = False
    while time.monotonic() < deadline:
        for slug in slugs:
            with mailboxes.using(slug), SessionLocal(slug) as db:
                rows = (db.query(InboundEmail).filter(match)
                        .order_by(InboundEmail.created_at.desc()).all())
                done = [row for row in rows if row.outcome != "received"]
                if done:
                    db.expunge_all()
                    if waited:
                        print()
                    return slug, done[0]
        time.sleep(3)
        print("         ." if not waited else ".", end="", flush=True)
        waited = True
    print()
    return None


def forget_receipts(slug: str, receipt_ids: set[str]) -> dict:
    from app import mailboxes
    from app.db import SessionLocal

    with mailboxes.using(slug), SessionLocal(slug) as db:
        files = raw_files_for(db, receipt_ids)
        gone = purge(db, "inbound_emails", receipt_ids)
    for path in files:
        path.unlink(missing_ok=True)
    return gone


def token_leg(r: Report, store: str, name: str, deliver, timeout: float, keep: bool) -> None:  # noqa: ANN001
    """Mint a reply token in the store, deliver to it by `deliver`, wait, clean up."""
    from app import mailboxes, outreach_send
    from app.db import SessionLocal
    from app.email_envelopes import is_our_address
    from app.models import InboundEmail, Lead, Outreach

    from app import profile

    with mailboxes.using(store):
        domain = profile.mail_domain()
    if not domain:
        r.skip(name, "no mail domain -- SENDING_DOMAIN is not set")
        return
    with mailboxes.using(store), SessionLocal(store) as db:
        lead = Lead(name="Liner live check", email="", phone="", source="email")
        db.add(lead)
        db.flush()
        token = outreach_send.mint_reply_token(db)
        db.add(Outreach(lead_id=lead.id, channel="email", direction="out", kind="follow_up",
                        to_address="live-check@live-check.invalid", subject="Liner live check",
                        body="A round trip from make live-check.", status="sent", reply_token=token))
        db.commit()
        lead_id = lead.id
    to = f"reply+{token}@{domain}"
    try:
        if not is_our_address(to):
            r.fail(name, f"{to} is not one of our addresses -- refusing to send to it")
            return
        started = time.monotonic()
        problem = deliver(to)
        if problem:
            r.fail(name, problem)
            return
        found = wait_for_receipt([store], InboundEmail.to_address.ilike(f"%{token}%"), timeout)
        if found is None:
            r.fail(name, f"nothing arrived for {to} in {timeout:.0f}s")
            return
        _, row = found
        r.check(row.outcome == "accepted" and row.matched_by == "reply_token", name,
                f"{to} -> filed in {store} by its reply token, {time.monotonic() - started:.0f}s",
                f"arrived but {row.outcome} ({row.matched_by or 'no match'}): {row.detail[:200]}")
    finally:
        if not keep:
            with mailboxes.using(store), SessionLocal(store) as db:
                receipts = {row.id for row in db.query(InboundEmail).filter(
                    InboundEmail.to_address.ilike(f"%{token}%")).all()}
                files = raw_files_for(db, receipts) if receipts else []
                gone = purge(db, "leads", {lead_id})
                if receipts:
                    gone.update(purge(db, "inbound_emails", receipts))
            for path in files:
                path.unlink(missing_ok=True)
            print(f"         removed: {gone}")


def check_intake(r: Report, store: str, keep: bool, intake: str = "") -> None:
    """The Worker's own request, made from here: the app half of a reply."""
    import httpx

    section("intake: a reply posted the way the Worker posts it")
    url = intake or worker_raw_url()

    def post_to_intake(to: str) -> str:
        if not url:
            return "no WEBHOOK_RAW_URL in wrangler.jsonc"
        raw = raw_message("Liner live check <live-check@live-check.invalid>", to,
                          f"Liner live check (app half) {uuid.uuid4().hex[:8]}")
        try:
            resp = httpx.post(url, content=raw, timeout=30, headers={
                "Content-Type": "message/rfc822",
                "X-Webhook-Secret": settings.webhook_secret,
                "X-Envelope-From": "live-check@live-check.invalid",
                "X-Envelope-To": to,
            })
        except Exception as exc:  # noqa: BLE001
            return f"{url}: {type(exc).__name__}: {str(exc)[:200]}"
        if resp.status_code != 200:
            return (f"{url} answered {resp.status_code}: {resp.text[:200]}"
                    + (" -- WEBHOOK_SECRET here differs from what that host runs on"
                       if resp.status_code == 401 else ""))
        return ""

    print(f"  the Worker posts to {worker_raw_url() or '(unknown)'} (this checkout's wrangler.jsonc)"
          + (f"; posting to {url} instead" if intake else ""))
    token_leg(r, store, "the intake files a reply by its token", post_to_intake, 30, keep)


def check_mail(r: Report, store: str, info: dict, timeout: float, keep: bool) -> None:
    from app import mailboxes, outreach_send
    from app.db import SessionLocal
    from app.email_envelopes import is_our_address
    from app.integrations.email.base import with_name
    from app.models import InboundEmail

    section("mail: round trips through Resend, Cloudflare, the Worker and the intake")
    sender = info["sender"]
    if not sender.delivers:
        r.skip("the whole route", f"EMAIL_SENDER={settings.email_sender} delivers nothing")
        return
    ops_from = sender.from_header(with_name(outreach_send.OPS_SENDER_NAME, sender.default_address("ops")))

    def send_to(to: str) -> str:
        result = sender.send(to, f"Liner live check {uuid.uuid4().hex[:8]}",
                             "A round trip from `make live-check`. It is removed once it has arrived.",
                             from_address=ops_from, headers={"Auto-Submitted": "auto-generated"})
        return "" if result.status == "sent" else f"the send failed: {result.detail[:300]}"

    token_leg(r, store, f"a buyer's reply comes back to {info['mail_domain']}", send_to, timeout, keep)

    # Liner's own mailbox, from the dealership's address -- both domains out.
    support = settings.support_email
    nonce = uuid.uuid4().hex[:10]
    if not is_our_address(support):
        r.fail("mail to Liner's support address", f"{support} is not one of our addresses")
        return
    # Sent inside the store, or the From falls back to the deployment's own.
    with mailboxes.using(store), SessionLocal(store) as db:
        dealer_from = outreach_send.dealership_from(db, sender)
        result = sender.send(support, f"Liner live check {nonce}",
                             "A round trip from `make live-check`. It is removed once it has arrived.",
                             from_address=dealer_from, headers={"Auto-Submitted": "auto-generated"})
    if result.status != "sent":
        r.fail(f"mail to {support}", f"the send from {dealer_from} failed: {result.detail[:300]}")
        return
    # Mail to us is filed in the default store and read at /ops.
    stores = [""] + [s for s in seeded_stores() if s != settings.dealership]
    found = wait_for_receipt(stores, InboundEmail.subject.ilike(f"%{nonce}%"), timeout)
    if found is None:
        r.fail(f"mail to {support} arrives", f"nothing in {timeout:.0f}s -- Email Routing for "
               f"{support.partition('@')[2]}, or the Worker")
        return
    slug, row = found
    r.check(slug == "" and row.lead_id is None, f"mail to {support} arrives, for /ops",
            f"from {dealer_from}, {row.outcome}",
            f"filed in {slug or 'the default store'} against a buyer: {row.detail[:160]}")
    if not keep:
        print(f"         removed: {forget_receipts(slug, {row.id})}")


# ------------------------------------------------------------------- widget


def check_widget(r: Report, store: str, base: str, info: dict) -> None:
    import httpx

    section("widget: the website chat, as the dealer's own site asks for it")
    try:
        loader = httpx.get(f"{base}/embed.js", timeout=20)
        r.check(loader.status_code == 200 and "javascript" in loader.headers.get("content-type", ""),
                "the loader is served", f"{base}/embed.js",
                f"{loader.status_code} {loader.headers.get('content-type')}")
        origins = info["embed"]
        if not origins:
            r.skip("their websites", "this profile lists no embed_origins")
        for origin in origins:
            cfg = httpx.get(f"{base}/api/widget/config", params={"origin": origin},
                            headers={"Origin": origin}, timeout=20).json()
            where = cfg.get("frame_origin") or "{0.scheme}://{0.netloc}".format(urlsplit(base))
            r.check(cfg.get("allowed") and cfg.get("enabled"), f"the chat shows on {origin}",
                    f"frame at {where}{cfg.get('frame')}",
                    cfg.get("reason") or json.dumps(cfg)[:200])
        stranger = httpx.get(f"{base}/api/widget/config", params={"origin": "https://example.com"},
                             headers={"Origin": "https://example.com"}, timeout=20).json()
        r.check(not stranger.get("allowed"), "and not on anybody else's", "https://example.com refused")
        frame = httpx.get(f"{base}/widget/{store}", timeout=20)
        policy = frame.headers.get("content-security-policy", "")
        r.check(frame.status_code == 200 and all(o in policy for o in origins),
                "the frame lets only their sites embed it", policy[:160],
                f"{frame.status_code}: {policy[:160] or 'no Content-Security-Policy'}")
    except Exception as exc:  # noqa: BLE001
        r.fail("the widget", f"{type(exc).__name__}: {str(exc)[:200]}")


# --------------------------------------------------------------------- plan


def plan(store: str, base: str, only: list[str], inbox: str, demo: bool, timeout: float) -> int:
    from app import mailboxes, profile

    with mailboxes.using(store):
        domain, address, embed = profile.mail_domain(), profile.mailbox_address(), profile.embed_origins()
    print(f"\nlive-check plan -- nothing is sent, written or fetched\n")
    print(f"  store     {store}\n  address   {base}\n  mode      {'demo' if demo else 'production'}")
    steps = {
        "chat": [f"POST {base}/api/chat/sessions, then one turn: {QUESTION!r}",
                 "remove the conversation"],
        "voice": [f"POST {base}/api/voice/sessions (OpenAI mints a call session)",
                  f"one text exchange over wss Realtime, model {settings.voice_model}: {SPOKEN!r}",
                  "remove the conversation"],
        "resend": [f"GET Resend's domain list for {settings.sending_domain or '(SENDING_DOMAIN)'}"
                   + (f" and {domain}" if domain and domain != settings.sending_domain else "")],
        "inbox": ([f"send to {inbox} from {address or '(dealership)'} and from {settings.support_email}, "
                   "through OUTBOUND_ONLY_TO"] if inbox else ["skipped: no INBOX="]),
        "intake": [f"post a message to {worker_raw_url() or '(WEBHOOK_RAW_URL)'} for "
                   f"reply+<token>@{domain or '(mail domain)'}; wait for the receipt",
                   "remove the lead, the send, the receipt and the raw file"],
        "mail": [f"send through the provider to reply+<token>@{domain or '(mail domain)'}; "
                 f"wait up to {timeout:.0f}s",
                 f"send to {settings.support_email}; wait up to {timeout:.0f}s",
                 "remove the lead, the sends, the receipts and the raw files"],
        "widget": [f"GET {base}/embed.js and {base}/widget/{store}",
                   f"GET the settings as {', '.join(embed) or '(no sites)'} and as a stranger"],
    }
    if demo:
        steps["mail"] = ["skipped: the demo sends no mail (its outbox is checked in config)"]
        steps["intake"] = ["skipped: no mail is routed to the demo"]
        steps["resend"] = ["skipped"]
    for name in SECTIONS:
        if name not in only:
            continue
        print(f"\n  {name}")
        for step in steps[name]:
            print(f"    - {step}")
    print()
    return 0


# --------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--store", default="")
    parser.add_argument("--base", default="", help="the store's public address")
    parser.add_argument("--only", default=",".join(SECTIONS))
    parser.add_argument("--inbox", default="")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--intake", default="",
                        help="post the app-half message here instead of the Worker's URL -- "
                             "http://127.0.0.1:8000/api/emails/inbound/raw leaves nginx and "
                             "Cloudflare out of it")
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="seconds to wait for a message to come back")
    args = parser.parse_args()

    only = [s.strip() for s in args.only.split(",") if s.strip()]
    unknown = [s for s in only if s not in SECTIONS]
    if unknown:
        raise SystemExit(f"unknown section(s): {', '.join(unknown)}; choose from {', '.join(SECTIONS)}")
    store = pick_store(args.store)
    base = base_for(store, args.base)
    if args.plan:
        return plan(store, base, only, args.inbox, args.demo, args.timeout)

    r = Report()
    info = check_config(r, store, base, args.demo)
    if "chat" in only:
        check_chat(r, store, base, args.keep)
    if "voice" in only:
        check_voice(r, store, base, args.keep)
    if "resend" in only and not args.demo:
        check_resend(r, store, info)
    if "inbox" in only and args.inbox:
        check_inbox(r, store, args.inbox, info)
    if "intake" in only and not args.demo:
        check_intake(r, store, args.keep, args.intake)
    if "mail" in only and not args.demo:
        check_mail(r, store, info, args.timeout, args.keep)
    if "widget" in only:
        check_widget(r, store, base, info)

    c = r.counts
    print(f"\n{c['PASS']} passed, {c['FAIL']} failed, {c['SKIP']} skipped\n")
    return 1 if c["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
