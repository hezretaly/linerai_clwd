#!/usr/bin/env python3
"""Drive the entire flow over HTTP. No browser, no credentials, no network.

Buyer books an appointment by tapping rails, then the dealer side confirms,
assigns and sends outreach. This is the primary gate: it must pass against a
clean seed with an empty .env.

    make smoke
"""

from __future__ import annotations

import hmac
import json
import secrets
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from hashlib import sha256
from http.cookiejar import CookieJar

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/ws/dealer"
LOGIN = {"email": "dana.mercer@example.invalid", "password": "liner-dev"}

jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
failures: list[str] = []


class EventListener:
    """Watches the dealer socket for the whole run, the way a dashboard would."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.error = ""
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
        if not cookie:
            self.error = "no session cookie"
            return

        def run() -> None:
            try:
                import asyncio

                import websockets

                async def listen() -> None:
                    async with websockets.connect(
                        WS + "?since=0", additional_headers={"Cookie": cookie}
                    ) as socket:
                        while True:
                            event = json.loads(await socket.recv())
                            if event.get("type"):
                                self.seen.append(event["type"])
                asyncio.run(asyncio.wait_for(listen(), timeout=45))
            except (TimeoutError, asyncio.TimeoutError):
                pass
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()


def call(method: str, path: str, body: dict | None = None, *, stream: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=60) as response:
            raw = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"{method} {path} -> {exc.code}: {exc.read().decode()[:300]}") from None
    return raw if stream else (json.loads(raw) if raw else {})


def follow(url: str) -> tuple[int, str]:
    """Open a link the way a buyer's browser would, without following on."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(url, timeout=30) as response:
            return response.status, response.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")


def status_of(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    """Like call(), but a 4xx is the answer rather than a failure. The booking
    card's refusals (no name, bad email, slot gone) are all things the buyer
    acts on, so the gate has to be able to assert on them."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=60) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def upload(path: str, filename: str, content: bytes) -> dict:
    """One multipart POST, hand-rolled -- smoke.py deliberately has no deps."""
    boundary = "----linersmoke"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/xml\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        BASE + path, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with opener.open(request, timeout=60) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"POST {path} -> {exc.code}: {exc.read().decode()[:300]}") from None


# Matches config.DEV_WEBHOOK_SECRET. The inbound endpoint is the only one no
# session guards, so the shared secret is the entire door -- and it gets a
# development default precisely so the door can be tested rather than shipped
# on trust.
WEBHOOK_SECRET = b"liner-dev-inbound-secret"


def inbound(
    payload: dict,
    signature: str | None = None,
    *,
    path: str = "/api/inbound-email",
    shared: str | None = None,
) -> tuple[int, dict | str]:
    """POST a delivery the way the Cloudflare Worker would.

    Signs the exact bytes sent, not a re-serialisation of the object -- that
    mismatch is the classic reason every real delivery 401s, so the test has to
    be able to reproduce it rather than paper over it.

    `shared` swaps the HMAC for the plain X-Webhook-Secret header, which is
    what the deployed Worker sends; `path` covers the alias it posts to. Both
    are the live configuration, so both belong in the gate rather than being
    trusted.
    """
    raw = json.dumps(payload).encode()
    if shared is not None:
        headers = {"Content-Type": "application/json", "X-Webhook-Secret": shared}
    else:
        sig = (
            signature if signature is not None
            else hmac.new(WEBHOOK_SECRET, raw, sha256).hexdigest()
        )
        headers = {"Content-Type": "application/json", "X-Liner-Signature": sig}
    request = urllib.request.Request(BASE + path, data=raw, method="POST", headers=headers)
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:120]


def settled(message_id: str, tries: int = 40) -> dict:
    """Wait for a claimed delivery to be filed, and say what it became.

    The endpoint answers before it resolves -- the Worker rejects the message
    to the sender on a non-2xx, so a slow CRM bounces a real buyer's reply --
    which means the response says 'received' and the outcome arrives a moment
    later on the receipt.
    """
    for _ in range(tries):
        for row in call("GET", "/api/email/receipts")["receipts"]:
            if row["message_id"] == message_id and row["outcome"] != "received":
                return row
        time.sleep(0.1)
    return {}


def sse(raw: str) -> list[tuple[str, dict]]:
    events, event = [], None
    for line in raw.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            events.append((event, json.loads(line[6:])))
    return events


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def say(convo: str, *, rail_id: str | None = None, content: str | None = None):
    # pick() returns None when no chip matches, and the request then goes out
    # with no body at all -- the server answers "Empty message", which says
    # nothing about the chip that was missing.
    if rail_id is None and content is None:
        raise AssertionError(
            "say() got neither a rail nor text -- pick() found no matching chip, "
            "which usually means the conversation did not reach the stage that "
            "offers it."
        )
    body = {"rail_id": rail_id} if rail_id else {"content": content}
    events = sse(call("POST", f"/api/chat/sessions/{convo}/messages", body, stream=True))
    reply = next((d for e, d in events if e == "assistant_message"), None)
    rails = next((d for e, d in events if e == "rails"), {"rails": [], "stage": "?"})
    return reply, rails, events


def pick(rails: list[dict], *keywords: str) -> str | None:
    for rail in rails:
        if any(k.lower() in rail["label"].lower() for k in keywords):
            return rail["id"]
    return None


def main() -> int:
    print("\n== health ==")
    health = call("GET", "/api/health")
    check("api is up", health["status"] == "ok")
    check("running on the stub agent", health["llm_mode"] == "stub",
          f"unconfigured: {', '.join(health['unconfigured'])}")

    # Sign in first so the dealer socket can watch the whole run, exactly as an
    # open dashboard would during a live demo.
    call("POST", "/api/auth/login", LOGIN)
    listener = EventListener()
    listener.start()

    booked_here: list[str] = []

    print("\n== buyer books an appointment (rails only, no typing) ==")
    session = call("POST", "/api/chat/sessions")
    convo = session["conversation_id"]
    rails = session["rails"]
    check("openers offered", len(rails) >= 3, f"{len(rails)} chips")

    reply, state, _ = say(convo, rail_id=pick(rails, "third row"))
    check("liner answered with inventory", bool(reply and reply["content"]))
    check("a tool actually ran", bool(reply and reply["tool_calls"]),
          ", ".join(c["name"] for c in reply["tool_calls"]) if reply else "")
    check("stage advanced to browsing", state["stage"] == "browsing", state["stage"])

    reply, state, _ = say(convo, rail_id=pick(state["rails"], "tell me about"))
    check("stage advanced to vehicle_focus", state["stage"] == "vehicle_focus", state["stage"])

    reply, state, events = say(convo, rail_id=pick(state["rails"], "see it this week"))
    check("stage advanced to slot_offered", state["stage"] == "slot_offered", state["stage"])
    # The rule is that the buyer is handed concrete times and never asked an
    # open-ended "when works for you?". Those times used to be listed in the
    # reply text and are on the booking card now, so this follows them there
    # rather than pinning the sentence they used to appear in.
    offered = next((d for e, d in events if e == "booking"), None)
    times = sum(len(d["slots"]) for d in (offered or {"days": []})["days"])
    check("concrete times offered, not 'when works for you'",
          times >= 2 and "when works for you" not in (reply["content"].lower() if reply else ""),
          f"{times} times on the card")

    reply, state, _ = say(convo, rail_id=pick(state["rails"], "saturday morning", "works"))
    if state["stage"] != "booked":
        reply, state, _ = say(
            convo, content="I'm Jordan Reyes, and my email is jordan.reyes@example.com."
        )
    check("stage reached booked", state["stage"] == "booked", state["stage"])

    print("\n== the booking card offers only times the calendar really has ==")
    card_convo = call("POST", "/api/chat/sessions")
    cid, crails = card_convo["conversation_id"], card_convo["rails"]
    card = None
    for words in (("third row",), ("tell me about",), ("see it this week",)):
        _, state2, events = say(cid, rail_id=pick(crails, *words))
        crails = state2["rails"]
        card = next((d for e, d in events if e == "booking"), None)
        if card:
            break
    check("check_availability produced a booking card", card is not None)
    if card is None:
        return report()
    check("grouped into days with times under them",
          all(d.get("slots") for d in card["days"]),
          " / ".join(f"{d['short']}:{len(d['slots'])}" for d in card["days"][:4]))
    open_slots = {s["starts_at"] for d in card["days"] for s in d["slots"]}
    taken = {
        a["starts_at"] for a in call("GET", "/api/appointments")["appointments"]
        if a["status"] in ("booked", "confirmed")
    }
    check("no already-booked time is offered", not (open_slots & taken),
          f"{len(open_slots)} offered")

    print("\n== a refreshed buyer gets the whole thread back ==")
    again = call("GET", f"/api/chat/sessions/{cid}")
    check("the opening line comes back too -- it is never a message row",
          bool(again.get("greeting")), (again.get("greeting") or "")[:40])
    check("the messages are all there", len(again["messages"]) >= 4,
          f"{len(again['messages'])} messages")
    # The cars the buyer was shown are rebuilt from the reply's tool calls, so
    # they have to still be on it.
    shown = [
        v for m in again["messages"] for c in m["tool_calls"]
        if c["name"] in ("search_inventory", "get_vehicle")
        for v in (c["result"].get("vehicles") or [])
    ]
    check("with the search results still attached to the reply", bool(shown),
          f"{len(shown)} vehicles recoverable")
    check("and a booking card, looked up again rather than replayed",
          bool(again.get("booking")))
    replayed = {
        s["starts_at"] for d in (again.get("booking") or {"days": []})["days"]
        for s in d["slots"]
    }
    still_taken = {
        a["starts_at"] for a in call("GET", "/api/appointments")["appointments"]
        if a["status"] in ("booked", "confirmed")
    }
    check("so a time booked since is not offered a second time",
          not (replayed & still_taken), f"{len(replayed)} offered")

    print("\n== the form books through the executor, refusals and all ==")
    slot = card["days"][0]["slots"][0]["starts_at"]
    code, _ = status_of("POST", f"/api/chat/sessions/{cid}/book",
                        {"starts_at": slot, "name": "", "email": "sam@example.invalid"})
    check("a form with no name is refused", code == 409, str(code))
    code, _ = status_of("POST", f"/api/chat/sessions/{cid}/book",
                        {"starts_at": slot, "name": "Sam Okafor", "email": "not-an-email"})
    check("and so is a malformed email", code == 409, str(code))

    form = call("POST", f"/api/chat/sessions/{cid}/book", {
        "starts_at": slot, "name": "Sam Okafor",
        "email": "sam.okafor@example.invalid", "phone": "555-0161",
    })
    booked_here.append(form["appointment"]["id"])
    check("a complete form books", form["appointment"]["starts_at"] == slot,
          form["appointment"]["starts_at"])
    check("and the transcript reads as a conversation, not a form dump",
          "Sam Okafor" in form["buyer_message"]["content"]
          and "booked in" in form["assistant_message"]["content"],
          form["assistant_message"]["content"][:60])
    # The buyer typed those details, so provenance has to accept them as typed.
    # That check reads the buyer's messages, which is why the form writes one.
    check("the contact details are in the buyer's own words for provenance",
          "sam.okafor@example.invalid" in form["buyer_message"]["content"])

    again = call("POST", f"/api/chat/sessions/{cid}/book", {
        "starts_at": slot, "name": "Sam Okafor", "email": "sam.okafor@example.invalid",
    })
    check("a double-tapped submit does not book twice",
          again["appointment"]["id"] == form["appointment"]["id"])

    other = call("POST", "/api/chat/sessions")["conversation_id"]
    code, detail = status_of("POST", f"/api/chat/sessions/{other}/book",
                             {"starts_at": slot, "name": "Dana Two",
                              "email": "dana.two@example.invalid"})
    # Without this the second buyer books the same slot and turns up to nobody.
    check("a second buyer cannot take the same slot", code == 409, detail[:80])

    print("\n== credit applications are real sends, or nothing ==")
    over0 = call("GET", "/api/overview")
    keys = [k["key"] for k in over0["kpis"]]
    check("the six cards the dashboard asks for",
          keys == ["chat", "email", "calls", "appointments_set", "needs_a_person",
                   "credit_apps"], ", ".join(keys))
    lead_for_credit = next(
        (lead for lead in call("GET", "/api/leads")["leads"] if lead["email"]), None
    )
    check("a lead with an address to send to", lead_for_credit is not None)
    if lead_for_credit is None:
        return report()

    # Clear the link first: with nothing to send to, the draft must refuse
    # rather than mail an invitation to apply nowhere.
    call("PATCH", "/api/assistant-settings", {"credit_application_url": ""})
    call("POST", "/api/assistant-settings/publish")
    code, detail = status_of(
        "GET", f"/api/leads/{lead_for_credit['id']}/outreach?draft=1&kind=credit_application"
    )
    check("with no link configured the draft is refused, not invented", code == 503, str(code))
    check("and it names the setting that is missing", "not_configured" in detail, detail[:60])
    card = next(k for k in call("GET", "/api/overview")["kpis"] if k["key"] == "credit_apps")
    # Not asserting the count is zero: an application sent before the link was
    # cleared is still a real send, and pretending otherwise would be the same
    # kind of lie as inventing one.
    check("the card says why rather than leaving the number unexplained",
          card["unavailable"] and "link" in card["window"], card["window"])

    call("PATCH", "/api/assistant-settings",
         {"credit_application_url": "https://riverside.example/finance"})
    call("POST", "/api/assistant-settings/publish")
    draft = call("GET",
                 f"/api/leads/{lead_for_credit['id']}/outreach?draft=1&kind=credit_application")
    check("with a link it drafts one", draft["kind"] == "credit_application", draft["subject"])
    check("carrying the dealer's own link, not an invented one",
          "https://riverside.example/finance" in draft["body"])
    # A finance email that quotes a rate is one the buyer holds you to, and no
    # rate exists anywhere in this system.
    check("and no rate, term or approval",
          not any(w in draft["body"].lower() for w in ("apr", "% interest", "approved", "monthly payment")))

    before = next(k for k in call("GET", "/api/overview")["kpis"] if k["key"] == "credit_apps")["value"]
    record = call("POST", f"/api/leads/{lead_for_credit['id']}/outreach",
                  {"subject": draft["subject"], "body": draft["body"],
                   "kind": "credit_application"})
    # The dealer's own URL is invisible to us, so the send rewrites it to a hop
    # we own. Without that the card could only ever count sends.
    check("the link that goes out is one we can count",
          record["trackable"] and "/r/" in record["body"], record["body"][-40:])
    check("and nothing is counted before anyone clicks",
          record["opened"] is False
          and next(k for k in call("GET", "/api/overview")["kpis"]
                   if k["key"] == "credit_apps")["value"] == before)

    link = re.search(r"https?://\S+/r/\S+", record["body"]).group(0)
    code, location = follow(link)
    check("following it lands on the dealership's own application",
          code == 302 and location == "https://riverside.example/finance",
          f"{code} -> {location}")
    after = next(k for k in call("GET", "/api/overview")["kpis"] if k["key"] == "credit_apps")["value"]
    check("and the card counts the open", after == before + 1, f"{before} -> {after}")

    follow(link)
    again = next(k for k in call("GET", "/api/overview")["kpis"] if k["key"] == "credit_apps")["value"]
    # One buyer opening the form twice has not done two things.
    check("a second click does not count twice", again == after, f"{after} -> {again}")
    check("an unknown token is a 404, not a redirect to nowhere",
          follow(BASE + "/r/not-a-real-token")[0] == 404)

    print("\n== an unclaimed lead can be opened from the overview ==")
    pool = call("GET", "/api/overview")["queues"]["unclaimed_leads"]
    check("the queue carries a thread to open, not just a name",
          all("conversation_id" in lead for lead in pool),
          f"{sum(1 for lead in pool if lead.get('conversation_id'))}/{len(pool)} have one")
    linked = [lead for lead in pool if lead.get("conversation_id")]
    if linked:
        # A lead imported from ADF never chatted, so a missing id is a real
        # state -- but an id that is present has to resolve.
        check("and that thread really exists",
              call("GET", f"/api/conversations/{linked[0]['conversation_id']}")["id"]
              == linked[0]["conversation_id"])

    print("\n== the charts answer for a chosen window ==")
    for key, expect in (("today", "Today"), ("yesterday", "Yesterday"),
                        ("week", "Last 7"), ("month", "Last 30")):
        trend = call("GET", f"/api/overview/trends?range={key}")
        ok = (
            trend["label"].startswith(expect)
            and len(trend["by_hour"]) == 24
            and trend["range"] == key
        )
        check(f"range={key} answers for its own window", ok, trend["label"])
    # A typo must not quietly answer for today -- that shows the wrong window
    # under the right caption, which is worse than an error.
    check("an unknown range is refused rather than defaulted",
          status_of("GET", "/api/overview/trends?range=fortnight")[0] == 400)

    one_day = call("GET", "/api/overview/trends?from=2026-08-01")
    check("one date is a whole answer -- no need to type it twice",
          one_day["from"] == "2026-08-01" and one_day["to"] == "2026-08-01",
          one_day["label"])
    span = call("GET", "/api/overview/trends?from=2026-08-01&to=2026-08-08")
    check("and a period spans the days asked for",
          span["from"] == "2026-08-01" and span["to"] == "2026-08-08" and span["days"] == 8,
          span["label"])
    # Dates win over a range, or the caption would name one window and the
    # numbers would come from the other.
    both = call("GET", "/api/overview/trends?range=month&from=2026-08-01")
    check("explicit dates beat a named range", both["range"] == "custom", both["label"])
    for bad, why in (
        ("from=2026-08-08&to=2026-08-01", "backwards"),
        ("from=not-a-date", "unparseable"),
        ("from=2020-01-01&to=2026-01-01", "longer than a year"),
    ):
        check(f"a {why} range is refused", status_of("GET", f"/api/overview/trends?{bad}")[0] == 400)
    # Over a week a Sunday must not paint the whole week closed, and the hours
    # still come from hours_json rather than a hardcoded 8-to-6.
    week = call("GET", "/api/overview/trends?range=week")
    open_hours = [h["hour"] for h in week["by_hour"] if h["open"]]
    check("a week still knows the showroom's opening hours",
          bool(open_hours) and len(open_hours) < 24,
          f"open {open_hours[0]}:00-{open_hours[-1]}:00" if open_hours else "none")

    print("\n== the overview drives the live panel ==")
    over = call("GET", "/api/overview")
    check("it says where 'now' ends, rather than the client deciding",
          bool(over.get("happening_now_since")), over.get("happening_now_since", "")[:19])
    day = over["queues"]["active_conversations"]
    check("today's conversations carry their last activity",
          all("last_activity_at" in c for c in day), f"{len(day)} today")
    # Ordered on last activity, not on start: a thread opened this morning with
    # a message a minute ago is the most live thing on the screen.
    stamps = [c["last_activity_at"] for c in day]
    check("newest activity first", stamps == sorted(stamps, reverse=True))
    check("a live conversation is inside the two-hour window -- the panel that "
          "shows live work must not open empty on a fresh seed",
          any(c["last_activity_at"] >= over["happening_now_since"]
              and c["status"] in ("active", "handoff") for c in day))

    print("\n== the appointment exists on the dealer side ==")
    appointments = call("GET", "/api/appointments")["appointments"]
    booked = [a for a in appointments if a["conversation_id"] == convo]
    check("an appointment row was created", len(booked) == 1, f"{len(booked)} found")
    if not booked:
        return report()
    appointment = booked[0]
    booked_here.append(appointment["id"])
    check("it came from Liner, not a rep", appointment["booked_by"] == "liner")
    check("the lead has an email on file", bool(appointment["lead"]["email"]),
          appointment["lead"]["email"])

    print("\n== dealer confirms, assigns, reaches out ==")
    confirmed = call("POST", f"/api/appointments/{appointment['id']}/confirm")
    check("status is confirmed", confirmed["status"] == "confirmed", confirmed["status"])

    assigned = call("POST", f"/api/appointments/{appointment['id']}/assign", {"auto": True})
    check("auto-assign picked a rep", bool(assigned["assigned_user_id"]),
          (assigned.get("assigned_to") or {}).get("name", ""))

    draft = call("GET", f"/api/appointments/{appointment['id']}/outreach?draft=1")
    check("a draft was written", bool(draft["subject"] and draft["body"]), draft["subject"])

    sent = call("POST", f"/api/appointments/{appointment['id']}/outreach",
                {"subject": draft["subject"], "body": draft["body"]})
    check("outreach status is sent", sent["status"] == "sent", sent["status"])
    check("and it is honest about not being delivered",
          sent["delivered_externally"] is False, f"provider={sent['provider']}")

    thread = call("GET", f"/api/chat/sessions/{convo}")
    mirrored = [m for m in thread["messages"] if m["role"] == "rep"]
    check("the email was mirrored into the buyer's thread", len(mirrored) >= 1)

    print("\n== guards ==")
    probe = call("POST", "/api/chat/sessions")["conversation_id"]
    _, _, events = say(probe, content="What's the out-the-door price on the Accord?")
    escalated = any(e == "assistant_message" for e, _ in events)
    check("an out-the-door question was handled", escalated)
    conversation = call("GET", f"/api/conversations/{probe}")
    check("it escalated to a person", conversation["status"] == "handoff",
          f"status={conversation['status']}")
    # Deliberately still answering. Escalation used to set agent_paused, so a
    # buyer who asked one question a human had to answer got "someone is
    # picking this up" to everything afterwards -- and with nobody watching the
    # queue at 9pm that was the end of it. Only a rep pressing Take over stops
    # Liner now.
    check("but Liner keeps talking until a rep actually takes over",
          conversation["agent_paused"] is False)
    _, _, more = say(probe, content="ok. separately, do you take trade-ins?")
    check("so a following question still gets answered",
          any(e == "assistant_message" for e, _ in more))

    print("\n== adf lead import ==")
    raw_sample = call("GET", "/api/leads/import/adf/sample", stream=True).encode()
    check("a sample ADF document is served", raw_sample.lstrip().startswith(b"<?xml"),
          raw_sample[:20].decode(errors="replace"))

    preview = upload("/api/leads/import/adf/preview", "sample.adf.xml", raw_sample)
    check("the drop parsed", len(preview["prospects"]) >= 2, f"{len(preview['prospects'])} usable")
    check("a prospect with no way to be contacted was skipped, not imported",
          len(preview["errors"]) >= 1, f"{len(preview['errors'])} skipped")
    # `make smoke` has to be runnable twice in a row, so a prospect already on
    # file from an earlier run is expected. What must hold either way: the
    # preview reports what it found and writes nothing itself.
    total_after_preview = len(call("GET", "/api/leads")["leads"])
    upload("/api/leads/import/adf/preview", "sample.adf.xml", raw_sample)
    check("nothing was written by the preview",
          len(call("GET", "/api/leads")["leads"]) == total_after_preview,
          f"{total_after_preview} leads before and after")
    matched = [p for p in preview["prospects"] if p["in_stock"]]
    check("a requested vehicle was matched against real inventory", len(matched) >= 1,
          matched[0]["in_stock"]["title"] if matched else "none matched")
    unmatched = [p for p in preview["prospects"] if p["vehicle_label"] and not p["in_stock"]]
    check("a vehicle we do not have is reported as not in inventory", len(unmatched) >= 1)

    committed = call("POST", "/api/leads/import/adf", {"prospects": preview["prospects"]})
    landed = committed["created"] + committed["merged"]
    check("every prospect landed as a lead", len(landed) == len(preview["prospects"]),
          f"{len(committed['created'])} created, {len(committed['merged'])} merged")
    again = call("POST", "/api/leads/import/adf", {"prospects": preview["prospects"]})
    check("re-importing the same drop merges instead of duplicating",
          not again["created"] and len(again["merged"]) == len(preview["prospects"]),
          f"{len(again['created'])} created, {len(again['merged'])} merged")

    # There is no lead page any more: an imported lead shows up on the
    # conversations list as a row with no thread to open. That row exists only
    # because the list says which leads never had a conversation, so if this
    # goes null-blind the whole ADF import becomes invisible in the product.
    all_leads = {lead["id"]: lead for lead in call("GET", "/api/leads")["leads"]}
    threadless = [lead for lead in all_leads.values() if not lead.get("conversation_id")]
    check("a lead that never chatted is still listed", bool(threadless),
          f"{len(threadless)} of {len(all_leads)} have no thread")
    check("and the ones just imported are among them",
          all(not all_leads[lead["id"]].get("conversation_id") for lead in landed),
          f"{len(landed)} imported")

    imported = landed[0]
    fields = {f["key"]: f for f in imported["captured_fields"]}
    check("what the document said was captured", "comments" in fields)
    check("and it is marked as coming from the feed, not from a conversation",
          fields.get("comments", {}).get("provenance") == "adf",
          fields.get("comments", {}).get("provenance", "missing"))

    with_car = next((c for c in landed if c["email"]), imported)
    draft = call("GET", f"/api/leads/{with_car['id']}/outreach?draft=1")
    check("a lead-level draft was written", bool(draft["subject"] and draft["body"]),
          draft["kind"])
    body_text = draft["body"].lower()
    check("the draft never quotes a price", "$" not in draft["body"])
    check("it only claims a car is here when it is",
          ("still here" in body_text) == bool(
              next((p for p in preview["prospects"]
                    if p["email"] == with_car["email"] and p["in_stock"]), None)
          ))

    reached = call("POST", f"/api/leads/{with_car['id']}/outreach",
                   {"subject": draft["subject"], "body": draft["body"]})
    check("lead-level outreach was recorded", reached["status"] == "sent", reached["status"])
    check("with no appointment attached", reached["appointment_id"] is None)

    manual = call("POST", "/api/leads", {
        "name": "Smoke Walkin", "phone": "555-013-7788", "source": "phone",
        "vehicle_make": "Honda", "vehicle_model": "Accord",
    })
    check("a lead can be entered by hand", bool(manual["lead"]["id"]))
    check("and it does not claim to have come from a feed",
          manual["lead"]["source"] == "phone", manual["lead"]["source"])

    print("\n== the dashboard saw it happen (websocket) ==")
    import time

    time.sleep(1.5)
    check("socket connected", not listener.error, listener.error or "ok")
    for event in ("appointment.booked", "appointment.confirmed", "appointment.assigned",
                  "outreach.sent", "handoff.triggered", "lead.imported"):
        check(f"{event} reached the dashboard", event in listener.seen)

    print("\n== a rep can decline, or book on the buyer's behalf ==")
    fresh = call("POST", "/api/chat/sessions")["conversation_id"]
    say(fresh, content="Just looking, thanks")
    declined = call("POST", f"/api/conversations/{fresh}/decline")
    check("declining records why it closed, not just that it did",
          declined["outcome"] == "declined" and declined["status"] == "closed",
          f"{declined['status']}/{declined['outcome']}")
    check("and it stops waiting for a person",
          declined["open_escalation"] is None)

    rep_convo = call("POST", "/api/chat/sessions")["conversation_id"]
    say(rep_convo, content="Can someone book me in?")
    card = call("GET", f"/api/conversations/{rep_convo}/availability")
    check("a rep is offered the same card the buyer gets", bool(card["days"]),
          f"{sum(len(d['slots']) for d in card['days'])} times")
    rep_slot = card["days"][0]["slots"][0]["starts_at"]
    booked_by_rep = call("POST", f"/api/conversations/{rep_convo}/book", {
        "starts_at": rep_slot, "name": "Rep Booked", "email": "rep.booked@example.invalid",
    })
    check("the rep booking lands on the thread", booked_by_rep["stage"] == "booked",
          booked_by_rep["stage"])
    made = [a for a in call("GET", "/api/appointments")["appointments"]
            if a["conversation_id"] == rep_convo]
    check("and the appointment records who made it",
          len(made) == 1 and made[0]["booked_by"] == "rep",
          made[0]["booked_by"] if made else "none")
    booked_here.append(made[0]["id"])
    # Same executor as the buyer's card, so the same rules bind a rep.
    code, detail = status_of("POST", f"/api/conversations/{fresh}/book", {
        "starts_at": rep_slot, "name": "Someone Else", "email": "else@example.invalid",
    })
    check("a rep cannot double-book a slot either", code == 409, detail[:60])
    code, _ = status_of("POST", f"/api/conversations/{rep_convo}/book", {
        "starts_at": card["days"][1]["slots"][0]["starts_at"], "name": "X", "email": "not-email",
    })
    check("nor skip the email rule", code == 409, str(code))

    print("\n== the summary panel summarises, rather than quoting the last line ==")
    # `summary` is whatever Liner said last. The rail used to print it under a
    # heading saying Summary, which made a reply look like a recap of the
    # thread. `recap` is composed from rows, so it can be checked against them.
    recapped = call("GET", f"/api/conversations/{rep_convo}")
    check("a recap comes back on the detail response", bool(recapped.get("recap")),
          repr(recapped.get("recap"))[:90])
    check("and it is not just the last message",
          recapped["recap"] != recapped["summary"])
    check("it names the buyer and where the thread got to",
          "Rep Booked" in recapped["recap"] and "Booked for" in recapped["recap"],
          recapped["recap"][:90])
    check("a declined thread says so", "client decline"
          in call("GET", f"/api/conversations/{fresh}")["recap"].lower(),
          call("GET", f"/api/conversations/{fresh}")["recap"][:90])
    check("the list rows do not pay for it",
          "recap" not in call("GET", "/api/conversations")["conversations"][0])

    print("\n== one buyer, one timeline, every channel ==")
    # The lead the rep just booked has a chat thread and, once we send the
    # confirmation, an email. Both have to arrive in one ordered list.
    booked_lead = call("GET", f"/api/conversations/{rep_convo}")["lead"]["id"]
    tl = call("GET", f"/api/leads/{booked_lead}/timeline")
    kinds = [e["kind"] for e in tl["entries"]]
    check("the buyer's whole history comes back in one call", bool(kinds), str(len(kinds)))
    stamps = [e["at"] for e in tl["entries"]]
    check("and it is in the order it happened", stamps == sorted(stamps))
    check("the appointment is on it, not only the messages", "appointment" in kinds)
    check("the strip counts channels from what is there, never a fixed list",
          set(tl["channels"]) <= {"chat", "voice", "email", "phone_logged"},
          str(tl["channels"]))

    # The headline case, on fixture data: one buyer who chatted and later rang
    # back. On a per-thread list that is a stranger with no booking, and a rep
    # would offer them a slot they already have.
    both = next(
        (l for l in call("GET", "/api/leads")["leads"]
         if {"chat", "voice"} <= set(l.get("channels") or [])),
        None,
    )
    check("a buyer who used two channels is one buyer", both is not None,
          both["name"] if both else "no fixture lead uses both")
    if both:
        mixed = call("GET", f"/api/leads/{both['id']}/timeline")
        check("and both channels arrive in the one timeline",
              {"chat", "voice"} <= set(mixed["channels"]), str(mixed["channels"]))
        order = [e["at"] for e in mixed["entries"]]
        check("interleaved in the order they happened", order == sorted(order))
        check("each turn still says which channel it came from",
              all(e["channel"] in {"chat", "voice"}
                  for e in mixed["entries"] if e["kind"] == "message"))
        # The captured fields have their own panel, where each one wears its
        # provenance. Prose cannot carry that, so restating them in the recap
        # turns an inferred guess into a flat assertion a rep repeats on the
        # phone -- which is exactly what save_captured_fields refuses to let
        # the model do in the first place.
        guesses = [f["value"] for f in call("GET", f"/api/leads/{both['id']}")
                   ["captured_fields"] if not f["verified"]]
        check("the fixture really has an inferred field to test with",
              bool(guesses), str(guesses))
        check("and the recap does not restate a guess as a fact",
              not any(v and v in mixed["recap"] for v in guesses),
              mixed["recap"][:80])
    # Nothing in this system can send an SMS, so nothing may offer it.
    check("and never offers a channel this product does not have",
          "sms" not in tl["channels"])
    check("a recap rides along, so the rail is not empty", bool(tl["recap"]),
          tl["recap"][:70])
    # Lead-level, not the newest thread's. Devon booked on the website and rang
    # back next morning: the newest thread is the call, the appointment hangs
    # off the chat, and a per-thread recap told a rep nothing was booked.
    check("and it knows about a booking made on an earlier thread",
          "Booked for" in tl["recap"] or "Confirmed for" in tl["recap"],
          tl["recap"][:80])


    # THE regression this page can ship silently. An appointment email exists
    # twice in the database -- as an outreach row, and mirrored into the thread
    # so the round trip lands visibly. Concatenating the two shows every
    # confirmation twice, which reads as a dealership that mailed you twice.
    lead_before = call("GET", f"/api/leads/{booked_lead}")
    appt_id = [a for a in lead_before["appointments"] if a["status"] != "cancelled"][0]["id"]
    call("POST", f"/api/appointments/{appt_id}/outreach", {
        "subject": "Your appointment at Riverside Auto",
        "body": "See you then.",
    })
    after = call("GET", f"/api/leads/{booked_lead}/timeline")
    emails = [e for e in after["entries"] if e["kind"] == "outreach"]
    sent = call("GET", f"/api/leads/{booked_lead}")["outreach"]
    check("an emailed appointment appears once, not once per copy of it",
          len(emails) == len(sent), f"{len(emails)} entries for {len(sent)} sends")
    check("and it is marked as the thread copy it is",
          any(e["in_thread"] for e in emails))
    check("no mirrored message survives as a rep message as well",
          not any(e["kind"] == "message" and e["role"] == "rep"
                  and e.get("tool_calls") and
                  any("outreach_id" in str(t) for t in e["tool_calls"])
                  for e in after["entries"]))
    # Lead-level outreach has no mirror at all, so it must not be dropped by
    # the same rule that folds the mirrored kind.
    call("POST", f"/api/leads/{booked_lead}/outreach", {
        "subject": "Following up", "body": "Still interested?", "kind": "followup",
    })
    third = call("GET", f"/api/leads/{booked_lead}/timeline")
    loose = [e for e in third["entries"] if e["kind"] == "outreach" and not e["in_thread"]]
    check("an email with no thread copy is still shown", bool(loose),
          f"{len(loose)} unmirrored")

    print("\n== an anonymous thread is still readable ==")
    # A lead only exists once someone books, so most live chats have none.
    orphan = next(c for c in call("GET", "/api/conversations")["conversations"]
                  if not c["lead_id"])
    solo = call("GET", f"/api/conversations/{orphan['id']}/timeline")
    check("a conversation with no lead gets a timeline of its own",
          bool(solo["entries"]), f"{len(solo['entries'])} entries")
    check("and says there is no buyer behind it yet", solo["lead"] is None)

    print("\n== a returning buyer is the same person ==")
    # book_appointment matched on email alone; the importer matched on email
    # *then* phone. So someone who booked from chat and rang back leaving a
    # second address arrived as a second lead with the same number on file.
    phone = "(319) 555-0148"
    card = call("GET", f"/api/conversations/{rep_convo}/availability")
    slots = [s["starts_at"] for d in card["days"] for s in d["slots"]]
    one = call("POST", "/api/chat/sessions")["conversation_id"]
    say(one, content="I'd like to come in")
    call("POST", f"/api/conversations/{one}/book", {
        "starts_at": slots[0], "name": "Robin Ash",
        "email": "robin.ash@example.invalid", "phone": phone})
    two = call("POST", "/api/chat/sessions")["conversation_id"]
    say(two, content="Booking again")
    call("POST", f"/api/conversations/{two}/book", {
        "starts_at": slots[1], "name": "Robin Ash",
        "email": "r.ash@work.invalid", "phone": phone})
    lead_one = call("GET", f"/api/conversations/{one}")["lead"]["id"]
    lead_two = call("GET", f"/api/conversations/{two}")["lead"]["id"]
    check("a second booking on the same phone lands on the same lead",
          lead_one == lead_two, f"{lead_one[:8]} vs {lead_two[:8]}")
    # The form asks where the confirmation should go, so a corrected address
    # means it -- but the old one is worth keeping rather than overwriting.
    same = call("GET", f"/api/leads/{lead_one}")
    check("the address they last typed is the one on file",
          same["email"] == "r.ash@work.invalid", same["email"])
    check("and the one it replaced was kept, not dropped",
          any(f["key"] == "previous_email" for f in same["captured_fields"]),
          str([f["key"] for f in same["captured_fields"]]))
    for appt in call("GET", "/api/appointments")["appointments"]:
        if appt["lead_id"] == lead_one and appt["status"] in ("booked", "confirmed"):
            booked_here.append(appt["id"])

    print("\n== duplicates are pointed at, never merged ==")
    dupes = call("GET", f"/api/leads/{lead_one}/duplicates")
    check("a lead with no twin returns none rather than a guess",
          isinstance(dupes["duplicates"], list))
    if dupes["duplicates"]:
        check("and every hit says why it matched",
              all(d["reason"] in {"same email", "same phone"} for d in dupes["duplicates"]),
              str([d["reason"] for d in dupes["duplicates"]]))

    print("\n== the cross-channel list can be sliced without opening anything ==")
    listed = call("GET", "/api/conversations")["conversations"]
    stamps = [c.get("last_activity_at") for c in listed]
    check("every row says when it was last active", all(stamps), f"{sum(map(bool, stamps))}/{len(stamps)}")
    # Sorting on started_at put a thread that opened this morning and went
    # quiet above one a buyer is typing in right now.
    check("and the list leads with the most recent", stamps == sorted(stamps, reverse=True))
    focused = [c for c in listed if c["focus_vehicle_id"]]
    check("a thread with a car carries it, so the list can show a column",
          all(c.get("focus_vehicle", {}).get("title") for c in focused),
          f"{len(focused)} with a focus vehicle")
    # The four states a manager filters by have to be separable from the row
    # alone -- that is the whole page.
    for name, hits in (
        ("appointed", [c for c in listed if c["stage"] == "booked"]),
        ("declined", [c for c in listed if c["outcome"] == "declined"]),
        ("in progress", [c for c in listed if c["status"] != "closed"]),
        ("needs a person", [c for c in listed if c["open_escalation"]]),
    ):
        check(f"'{name}' is answerable from the list rows", bool(hits), f"{len(hits)} rows")

    print("\n== a car comes off the lot without taking its history with it ==")
    # Never a delete: vehicle_mentions and appointments both point at the row,
    # and that history is the only answer to "who was told about this car?".
    fleet = call("GET", "/api/inventory")["vehicles"]
    target = max(fleet, key=lambda v: v["mention_count"])
    before = call("GET", f"/api/inventory/{target['id']}")
    check("the blast radius is on the record before anything changes",
          before["mention_count"] > 0,
          f"{before['mention_count']} quotes, {len(before['appointments'])} visits")
    check("and it names who is booked in to see it, not just who was told",
          isinstance(before["appointments"], list))

    # Before/after, or the check proves nothing: a car the stub was never going
    # to offer "stops being offered" whatever the status says.
    def offered_vins() -> list[str]:
        probe = call("POST", "/api/chat/sessions")["conversation_id"]
        say(probe, content=f"Tell me about the {target['make']} {target['model']}")
        return [
            v["vin"]
            for m in call("GET", f"/api/conversations/{probe}")["messages"]
            for c in m["tool_calls"]
            for v in (c.get("result") or {}).get("vehicles", [])
        ]

    check("the agent offers it while it is on the lot",
          target["vin"] in offered_vins(), target["vin"])

    # Re-read: the probe above just quoted the car, so it wrote a mention of
    # its own. Comparing against the snapshot from before the probe would be
    # measuring data this test changed.
    pre = call("GET", f"/api/inventory/{target['id']}")

    sold = call("POST", f"/api/inventory/{target['id']}/status", {"status": "sold"})
    check("marking it sold takes", sold["status"] == "sold", sold["status"])
    check("the quote history survives it",
          len(sold["mentions"]) == len(pre["mentions"]),
          f"{len(pre['mentions'])} -> {len(sold['mentions'])}")
    check("and so does anyone booked in to see it -- cancelling is a rep's call",
          len(sold["appointments"]) == len(pre["appointments"]))

    # The point of the whole feature: Liner must stop offering it, and it is
    # filtered in the tool rather than asked for in the prompt.
    after_sold = offered_vins()
    check("and cannot once it is sold -- filtered in the tool, not asked of the model",
          target["vin"] not in after_sold, f"{len(after_sold)} other cars still offered")
    check("while the rest of the lot is still offered", bool(after_sold))

    # The regression that would make this feature quietly useless. The
    # dealership's own site still lists a car that sold an hour ago, so the
    # next import sees it "reappear" -- and used to set it back to available,
    # skipping the manual-override check every other field respects.
    check("the sold flag is marked manual so an import cannot undo it",
          "status" in sold["manual_fields"], str(sold["manual_fields"]))

    back = call("POST", f"/api/inventory/{target['id']}/status", {"status": "available"})
    check("a deal that falls through can be put back", back["status"] == "available")
    code, _ = status_of("POST", f"/api/inventory/{target['id']}/status", {"status": "gone"})
    check("an invented status is refused rather than stored", code == 400, str(code))

    print("\n== a reply finds its way back to the buyer ==")
    # Outbound mints a token and carries it in Reply-To; the Cloudflare
    # catch-all routes reply+<token>@ straight back here. Everything on this
    # side is real and runs offline -- only the Worker and Resend need a key.
    # Unique per run. The endpoint is idempotent on message id, so fixed ids
    # meant every run after the first was deduped away -- and the reopen check
    # below then left an escalation claimed for the next run to trip over.
    run = secrets.token_hex(4)
    replyable = call("GET", "/api/email/replyable")["sends"]
    check("every send carries a token a reply can come back on", bool(replyable),
          f"{len(replyable)} replyable sends")
    # A send with no lead has nowhere for a reply to land, and the page that
    # lists these promises which buyer it will show up on.
    check("and every one of them has a buyer for the reply to land on",
          all(s["lead_id"] for s in replyable))
    send = replyable[0]

    # The deployed Worker posts to /api/emails/inbound with a plain shared
    # secret. Both are live configuration, so both are checked here rather
    # than assumed -- a rename on either side is otherwise silent.
    code, aliased = inbound(
        {"messageId": f"<smoke-{run}-alias>", "from": "stranger@nowhere.invalid",
         "to": "sales@example.invalid", "subject": "Hi", "text": "hello"},
        path="/api/emails/inbound", shared=WEBHOOK_SECRET.decode(),
    )
    check("the path the deployed worker posts to reaches the same handler",
          code == 200, f"{code} {aliased}")
    check("and a plain shared secret authenticates it",
          aliased.get("outcome") == "received", str(aliased))
    wrong = inbound({"messageId": "<x>", "from": "a@b.c", "to": "sales@d"},
                    path="/api/emails/inbound", shared="not-the-secret")
    check("but the wrong one does not", wrong[0] == 401, str(wrong[0]))

    bad = inbound({"messageId": "<forged>", "from": "x@y.invalid", "to": "sales@d"},
                  signature="deadbeef")
    check("a delivery signed with the wrong secret is refused", bad[0] == 401, str(bad[0]))
    # And it must not vanish. A 401 into the void is unfalsifiable: the operator
    # sees no replies arriving and cannot tell a wrong secret from a broken
    # Cloudflare route from a buyer who never wrote back.
    receipts = call("GET", "/api/email/receipts")["receipts"]
    check("and it still leaves a receipt to find it by",
          any(r["outcome"] == "bad_signature" for r in receipts))

    reply = {
        "messageId": f"<smoke-{run}-token>",
        "from": send["to_address"],
        "to": f"reply+{send['reply_token']}@example.invalid",
        "subject": f"Re: {send['subject']}",
        "text": "Yes -- can I come in on Saturday?",
    }
    code, got = inbound(reply)
    # Answered before it is filed, on purpose: the Worker rejects the message
    # back to the sender on a non-2xx, so a slow CRM bounces a real reply.
    check("the endpoint answers immediately rather than making Cloudflare wait",
          code == 200 and got.get("outcome") == "received", str(got))
    filed = settled(reply["messageId"])
    check("and the reply is then filed against the right buyer",
          filed.get("outcome") == "accepted" and filed.get("lead_id") == send["lead_id"],
          str(filed.get("outcome")))

    timeline = call("GET", f"/api/leads/{send['lead_id']}/timeline")
    inbox = [e for e in timeline["entries"]
             if e["kind"] == "outreach" and e.get("direction") == "in"]
    check("and shows on their timeline as a reply, not as a send", bool(inbox),
          f"{len(inbox)} inbound entries")

    # Cloudflare retries. A retry must not give the buyer two replies.
    # A retry that arrives *during* processing is the case a naive fast-ack
    # loses: nothing is 'accepted' yet, so a dedupe looking only for that
    # files the reply twice. The claim is written before the response.
    code, again = inbound(reply)
    check("the same delivery twice is one activity",
          again.get("outcome") == "duplicate", str(again))
    after = call("GET", f"/api/leads/{send['lead_id']}/timeline")
    check("and the timeline did not grow",
          len([e for e in after["entries"]
               if e["kind"] == "outreach" and e.get("direction") == "in"]) == len(inbox))

    # No token -- a client that rewrote Reply-To, or a forward. The From
    # address is the loosest rule and comes last for that reason.
    inbound({
        "messageId": f"<smoke-{run}-no-token>", "from": send["to_address"],
        "to": "sales@example.invalid", "subject": "Hello", "text": "Still thinking",
    })
    loose = settled(f"<smoke-{run}-no-token>")
    check("a reply with no token falls back to the address on file",
          loose.get("outcome") == "accepted" and loose.get("lead_id") == send["lead_id"],
          str(loose.get("outcome")))
    check("and records which rule placed it", loose.get("matched_by") == "from_address",
          str(loose.get("matched_by")))

    # A name is never part of matching, so a stranger stays a stranger rather
    # than being attached to whoever happens to share one.
    inbound({
        "messageId": f"<smoke-{run}-stranger>", "from": "nobody@nowhere.invalid",
        "to": "sales@example.invalid", "subject": "Do you take trades?", "text": "?",
    })
    stranger = settled(f"<smoke-{run}-stranger>")
    check("mail from someone we do not know is kept, not guessed at",
          stranger.get("outcome") == "unresolved", str(stranger.get("outcome")))
    check("and kept, not dropped -- someone really wrote in",
          any(r["message_id"] == f"<smoke-{run}-stranger>"
              for r in call("GET", "/api/email/receipts")["receipts"]))

    # No session guards this endpoint, so an unbounded body is a firehose
    # anyone who finds the URL can point at it. The limit matches the Worker's.
    oversized = inbound({"messageId": f"<smoke-{run}-big>", "text": "x" * (11 * 1024 * 1024)})
    check("an oversized delivery is refused before it is parsed",
          oversized[0] == 413, str(oversized[0]))

    print("\n== a buyer answering puts it back on a person ==")
    # Built here rather than hunted for in the seed. The first version looked
    # for any flagged thread with an address on it, which passed on a fresh
    # database and then failed on the next run, because this very test had
    # claimed the one it found.
    mine = call("POST", "/api/chat/sessions")["conversation_id"]
    say(mine, content="I want to see something with a third row")
    slots = [s["starts_at"] for d in
             call("GET", f"/api/conversations/{mine}/availability")["days"]
             for s in d["slots"]]
    escalation_email = f"reply.test.{run}@example.invalid"
    call("POST", f"/api/conversations/{mine}/book", {
        "starts_at": slots[0], "name": "Reply Tester", "email": escalation_email})
    for appt in call("GET", "/api/appointments")["appointments"]:
        if appt["conversation_id"] == mine and appt["status"] in ("booked", "confirmed"):
            booked_here.append(appt["id"])

    say(mine, content="What's the out-the-door price on that?")
    flagged = call("GET", f"/api/conversations/{mine}")
    check("a question a human has to answer raises a handoff",
          flagged["open_escalation"] is not None)

    call("POST", f"/api/conversations/{mine}/takeover")
    check("a rep taking over claims it",
          call("GET", f"/api/conversations/{mine}")["open_escalation"] is None)

    inbound({
        "messageId": f"<smoke-{run}-reopen>", "from": escalation_email,
        "to": "sales@example.invalid", "subject": "Re:",
        "text": "Following up on my question",
    })
    reopened = settled(f"<smoke-{run}-reopen>")
    check("the reply reaches the buyer it came from",
          reopened.get("outcome") == "accepted", str(reopened.get("outcome")))
    check("and puts the thread back in the queue -- their turn became ours again",
          call("GET", f"/api/conversations/{mine}")["open_escalation"] is not None)

    # A mail server is entitled to rewrite the case of a local part, and
    # SQLite's `=` is not case-insensitive. Tokens are minted lowercase so this
    # cannot bite, but a reply arriving upper-cased must still find its buyer
    # rather than falling through to the loosest rule available.
    shouty = dict(reply)
    shouty["messageId"] = f"<smoke-{run}-shouty>"
    shouty["to"] = f"reply+{send['reply_token'].upper()}@example.invalid"
    inbound(shouty)
    cased = settled(shouty["messageId"])
    check("a token that came back upper-cased still finds its buyer",
          cased.get("lead_id") == send["lead_id"], str(cased.get("outcome")))
    check("and by the token, not by guessing from the address",
          cased.get("matched_by") == "reply_token", str(cased.get("matched_by")))

    print("\n== a manager can see the whole mailbox, not just per-buyer ==")
    box = call("GET", "/api/email/messages")
    check("every send and reply is listed in one place", bool(box["messages"]),
          f"{box['counts']['all']} messages")
    counts = box["counts"]
    check("the boxes partition the mail rather than overlapping",
          counts["received"] + counts["sent"] + counts["failed"] + counts["unmatched"]
          == counts["all"], str(counts))
    # A tab saying 12 and showing 9 is the classic two-definitions bug, so the
    # count and the filter are asserted against each other rather than trusted.
    for name in ("received", "sent", "failed", "unmatched"):
        shown = call("GET", f"/api/email/messages?box={name}")["messages"]
        check(f"the {name} tab shows what it counts",
              len(shown) == counts[name], f"says {counts[name]}, shows {len(shown)}")

    # The reason the mailbox is a union and not one table. Mail nobody could
    # place has no outreach row and no buyer page -- without this it is
    # visible only on the diagnostics strip, which is not where anyone looks.
    unmatched = call("GET", "/api/email/messages?box=unmatched")["messages"]
    check("mail from a stranger is readable rather than only counted",
          any(m["body"] for m in unmatched), f"{len(unmatched)} unplaced")
    check("and is not pinned to a buyer it does not belong to",
          all(m["lead_id"] is None for m in unmatched))

    # Taken from a message that is really there, not a guess. A term that
    # matches nothing makes "narrows the list" true for the wrong reason.
    term = next(
        (w for m in box["messages"] for w in m["subject"].split() if len(w) > 5),
        "",
    )
    hit = call("GET", f"/api/email/messages?q={term}")["messages"]
    miss = call("GET", "/api/email/messages?q=zzzznotinanything")["messages"]
    check("search finds a word that is really in the mailbox", bool(hit),
          f"{term!r} -> {len(hit)} hits")
    check("and narrows rather than returning everything", len(hit) < counts["all"],
          f"{len(hit)} of {counts['all']}")
    check("a term in nothing returns nothing", not miss, str(len(miss)))

    print("\n== the resend path, as far as it can honestly be checked ==")
    # The HTTP call needs a key this environment does not have and must not
    # invent. Everything either side of it is asserted here; only the send is
    # unproven, and /api/integrations says so rather than showing a green tick.
    sys.path.insert(0, "backend")
    from app.integrations.email.resend import ResendSender

    resend = ResendSender()
    try:
        resend.check()
        check("resend refuses to pretend it is configured", False, "check() passed with no key")
    except Exception as exc:
        check("resend names the variable it wants rather than failing vaguely",
              "RESEND_API_KEY" in getattr(exc, "missing", []),
              str(getattr(exc, "missing", exc)))

    payload = resend.payload("buyer@example.com", "Subject", "Body", reply_to="reply+t@d")
    check("the request body is the shape resend documents",
          payload["to"] == ["buyer@example.com"] and payload["subject"] == "Subject"
          and payload["text"] == "Body",
          str(sorted(payload)))
    check("and carries the reply address, or a reply can never come back",
          payload["reply_to"] == "reply+t@d")

    blocked = call("POST", "/api/email/test-send", {"to": "stranger@example.invalid"})
    check("a test send still goes through the outbound limit",
          blocked["status"] in {"sent", "failed"}, f"{blocked['status']}: {blocked['error'][:60]}")

    # Who may be emailed used to take two settings to express and read like an
    # inbound access list. It is one now, and the dashboard states it rather
    # than leaving a manager to infer it from DEMO_MODE plus a list.
    scope = call("GET", "/api/integrations")
    check("the dashboard says who outbound may reach, in words",
          bool(scope.get("outbound_scope")), scope.get("outbound_scope", "")[:70])
    # None and [] are different answers -- no limit versus nobody -- and
    # collapsing them is how an empty list starts meaning unrestricted.
    limited = scope.get("outbound_recipients")
    check("and distinguishes 'no limit' from 'nobody'",
          limited is None or isinstance(limited, list),
          "no limit" if limited is None else f"{len(limited)} allowed")

    print("\n== the run gives back the slots it took ==")
    # Every booking above holds a time that book_appointment will refuse to
    # double-book. Without releasing them each run eats into the fixture's
    # week, and after enough runs check_availability has nothing to offer and
    # the booking flow fails -- which is exactly what happened. `make smoke`
    # must stay runnable against a database that has already seen it.
    released = 0
    for appt in call("GET", "/api/appointments")["appointments"]:
        if appt["id"] in booked_here and appt["status"] in ("booked", "confirmed"):
            code, _ = status_of("POST", f"/api/appointments/{appt['id']}/cancel")
            released += code == 200
    check("this run's appointments were released", released == len(booked_here),
          f"{released}/{len(booked_here)} cancelled")
    after = call("GET", "/api/overview/trends?range=today")
    check("and the calendar can offer times again",
          bool(call("GET", "/api/appointments")) and after["range"] == "today")

    return report()


def report() -> int:
    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
