#!/usr/bin/env python3
"""Drive the entire flow over HTTP. No browser, no credentials, no network.

Buyer books an appointment by tapping rails, then the dealer side confirms,
assigns and sends outreach. This is the primary gate: it must pass against a
clean seed with an empty .env.

    make smoke
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import pathlib
import shutil
import secrets
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
from urllib.parse import quote
from hashlib import sha256
from http.cookiejar import CookieJar

BASE = "http://127.0.0.1:8000"
TSX = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src" / "main.tsx"
WS = "ws://127.0.0.1:8000/ws/dealer"
LOGIN = {"email": "dana.mercer@example.invalid", "password": "liner-dev"}
# The other seeded login, and the only one that is not a manager. Assigning a
# buyer to somebody else is a manager's act now, so proving that takes a
# session that is not one.
REP_LOGIN = {"email": "marcus.vale@example.invalid", "password": "liner-dev"}

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


def upload_audio(convo: str, content_type: str, blob: bytes, duration_ms: int = 0,
                 *, complete: bool = True, seq: int = 0, track: str = "call") -> dict:
    """Post one slice of call audio the way the buyer's browser does.

    Streamed rather than uploaded whole: the end of a call is the least
    reliable moment there is, and everything up to the last slice is on disk
    before it arrives.
    """
    boundary = "----lineraudio"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="call"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{BASE}/api/voice/recording/{convo}/chunk?seq={seq}&track={track}",
        data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with opener.open(request, timeout=60) as response:
            stored = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return {"stored": False, "status": exc.code, "detail": exc.read().decode()[:120]}
    if complete:
        return call("POST", f"/api/voice/recording/{convo}/complete"
                            f"?duration_ms={duration_ms}")
    return stored


def anonymous_status(path: str) -> int:
    """What a stranger with the URL and no cookie gets. Its own opener, since
    the shared one is signed in for the rest of the run."""
    try:
        with urllib.request.build_opener().open(BASE + path, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def fetch_audio(convo: str, *, anonymous: bool = False) -> tuple[int, bytes]:
    """Play a call back. `anonymous` skips the cookie jar, which is the check
    that matters: the file is a buyer's voice and must not be public."""
    request = urllib.request.Request(f"{BASE}/api/voice/recording/{convo}")
    fetcher = urllib.request.build_opener() if anonymous else opener
    try:
        with fetcher.open(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


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


def utcnow_local():
    """The same naive UTC the app stamps rows with."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _raises_keyerror(module) -> bool:
    """A flag vocabulary that silently accepted anything would be a place to
    hide configuration nobody can find again."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            module.set(db, "not-a-real-flag", "on")
        except KeyError:
            return True
    return False


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


#: Every slot this run took, released at the end -- and released even when the
#: run does not reach the end.
#:
#: `book_appointment` refuses a clash, and the fixture week holds about twenty
#: slots. The release used to be the last section of `main`, so it ran only on
#: a clean pass: any run that failed part-way kept its bookings forever. That
#: makes a failure self-reinforcing -- each aborted run leaves fewer slots, so
#: the next one is likelier to abort earlier, and eventually a change in one
#: corner of the system shows up as "0 times on the card" in a section that
#: has nothing to do with it. It took thirty-six stranded appointments to
#: notice, and by then the failure named the wrong culprit.
booked_here: list[str] = []


def release_slots() -> int:
    """Give back every slot this run took. Safe to call twice."""
    released = 0
    for appt in call("GET", "/api/appointments")["appointments"]:
        if appt["id"] in booked_here and appt["status"] in ("booked", "confirmed"):
            code, _ = status_of("POST", f"/api/appointments/{appt['id']}/cancel")
            released += code == 200
    return released


def main() -> int:
    print("\n== health ==")
    health = call("GET", "/api/health")
    check("api is up", health["status"] == "ok")
    check("running on the stub agent", health["llm_mode"] == "stub",
          f"unconfigured: {', '.join(health['unconfigured'])}")

    # The door is shut unless somebody opened it. PUBLIC_DEMO hands a stranger
    # every buyer name, phone number, transcript and call recording in the
    # database, so the value that matters most about it is the default -- and
    # a default is exactly the kind of thing that gets flipped by a merge and
    # noticed by nobody.
    door = call("GET", "/api/auth/public")
    if door["available"]:
        # Someone turned it on for this run. Say so rather than failing: a
        # public deployment is a supported configuration, and a gate that goes
        # red on it teaches people to ignore the gate. What still has to hold
        # is that the door leads in as a rep.
        check("PUBLIC_DEMO is ON for this run -- it opens as a sales rep, never a manager",
              door.get("role") == "rep", str(door))
    else:
        check("the public door is shut unless somebody opened it",
              door["available"] is False, str(door))
        check("and there is no door to walk through while it is",
              status_of("POST", "/api/auth/public", {})[0] == 404)
    # True either way, and the more important half: opening the door does not
    # authenticate anybody by itself. A visitor is signed in only by asking to
    # be, which keeps one notion of "who is signed in" for the whole system.
    check("a dashboard request with no session is refused either way",
          anonymous_status("/api/overview") == 401, str(anonymous_status("/api/overview")))

    # Sign in first so the dealer socket can watch the whole run, exactly as an
    # open dashboard would during a live demo.
    call("POST", "/api/auth/login", LOGIN)
    listener = EventListener()
    listener.start()

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
    # Recorded so the release at the end gives this slot back too. It was the
    # one booking in the script that never was, because it is made through the
    # rails rather than by calling book_appointment directly -- so every run,
    # passing or failing, quietly ate one of the fixture week's twenty slots
    # and the failure surfaced runs later as "0 times on the card" here.
    for appt in call("GET", "/api/appointments")["appointments"]:
        if appt.get("conversation_id") == convo and appt["status"] in ("booked", "confirmed"):
            booked_here.append(appt["id"])

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
    check("the cards the dashboard asks for, in order",
          keys == ["chat", "email", "calls", "voice_spend", "appointments_set",
                   "needs_a_person", "credit_apps"], ", ".join(keys))
    # Money, not a tally. A card that rendered a dollar amount as a count would
    # read as five hundred calls rather than five hundred dollars.
    spend = next(k for k in over0["kpis"] if k["key"] == "voice_spend")
    check("and voice spend says it is money rather than a count",
          spend.get("format") == "usd", str(spend.get("format")))
    # $0.00 on this card would read as "calls are free", which is the most
    # expensive thing this dashboard could imply.
    check("with nothing billed yet, it says so instead of showing zero",
          spend["unavailable"] == (spend["value"] == 0),
          f"{spend['value']} / {spend['window']}")
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

    print("\n== assigning a buyer settles every panel that was asking for one ==")
    # The overview asks three questions about the same people -- who needs a
    # person, what is happening, and who belongs to nobody -- and they used to
    # be three unconnected facts. A rep could assign a lead and still find them
    # in Needs a person, which is two panels disagreeing about whether somebody
    # is being looked after.
    rep = next(m for m in call("GET", "/api/team")["members"] if m["role"] == "rep")
    flagged = next(
        (e for e in call("GET", "/api/overview")["queues"]["needs_a_person"] if e.get("lead")),
        None,
    )
    check("the seed has a buyer waiting for a person", flagged is not None)
    owner = flagged["lead"]["id"]
    # Set the starting state rather than assume it -- earlier sections of this
    # run assign leads, and a check that only holds when it happens to run
    # first is a check that will fail for the wrong reason later.
    call("POST", f"/api/leads/{owner}/assign", {"user_id": None})
    check("and while nobody owns them they sit in the unclaimed queue too",
          any(l["id"] == owner for l in call("GET", "/api/overview")["queues"]["unclaimed_leads"]),
          "in both queues")

    # Whether Liner was already held on this thread is not this section's
    # business -- a rep may have taken it over long before. What is being
    # checked is that *assigning* does not change it, so the state before is
    # what the state after is compared against.
    held_before = call("GET", f"/api/conversations/{flagged['conversation_id']}")["agent_paused"]

    given = call("POST", f"/api/leads/{owner}/assign", {"user_id": rep["id"]})
    check("assigning a buyer gives them an owner",
          given["assigned_to"]["id"] == rep["id"], str(given.get("assigned_to"))[:60])
    check("and claims what they had waiting, because a person has been found",
          given["escalations_claimed"] >= 1, str(given["escalations_claimed"]))

    queues = call("GET", "/api/overview")["queues"]
    check("so they leave Needs a person",
          not any((e.get("lead") or {}).get("id") == owner for e in queues["needs_a_person"]))
    check("and leave the unclaimed queue in the same move",
          not any(l["id"] == owner for l in queues["unclaimed_leads"]))

    # Assigning is not taking over. Liner keeps answering everything else while
    # the rep gets to them -- the same reason escalating does not gag it.
    thread = call("GET", f"/api/conversations/{flagged['conversation_id']}")
    check("but Liner is not silenced by it -- that is a separate decision",
          thread["agent_paused"] == held_before, f"{held_before} -> {thread['agent_paused']}")

    # And back the other way: a rep who takes a thread over takes the buyer.
    loose = next(
        (c for c in call("GET", "/api/conversations")["conversations"]
         if c.get("lead") and not c["lead"].get("assigned_to") and c["status"] != "closed"),
        None,
    )
    if loose:
        call("POST", f"/api/conversations/{loose['id']}/takeover")
        check("taking a thread over takes its buyer out of the unclaimed queue",
              not any(l["id"] == loose["lead"]["id"]
                      for l in call("GET", "/api/overview")["queues"]["unclaimed_leads"]),
              loose["lead"]["name"])
        call("POST", f"/api/conversations/{loose['id']}/handback")

    # Putting somebody back is the same endpoint with no user. What it does not
    # do is reopen the escalation: somebody really did pick that up, and taking
    # the buyer off them later does not un-happen it.
    back = call("POST", f"/api/leads/{owner}/assign", {"user_id": None})
    check("a buyer can be put back in the queue",
          back["assigned_to"] is None, str(back.get("assigned_to")))
    check("but work already claimed stays claimed, not reopened",
          not any((e.get("lead") or {}).get("id") == owner
                  for e in call("GET", "/api/overview")["queues"]["needs_a_person"]))
    check("assigning to somebody who does not exist is refused",
          status_of("POST", f"/api/leads/{owner}/assign", {"user_id": "nobody"})[0] == 404)

    # An escalation raised *after* somebody was assigned used to arrive
    # unclaimed, so a buyer wore "Needs a person" next to the name of the
    # person who had them and a manager could not tell a failed assignment
    # from a lying badge. The rule is one rule now (app/escalations.py) and it
    # has to hold at both ends, not just at the moment of assigning.
    tag = secrets.token_hex(4)
    owned = call("POST", "/api/chat/sessions")["conversation_id"]
    say(owned, content="I want to see something with a third row")
    owned_slots = [t["starts_at"] for d in
                   call("GET", f"/api/conversations/{owned}/availability")["days"]
                   for t in d["slots"]]
    call("POST", f"/api/conversations/{owned}/book", {
        "starts_at": owned_slots[0], "name": "Owned Buyer",
        "email": f"owned.buyer.{tag}@example.invalid"})
    for appt in call("GET", "/api/appointments")["appointments"]:
        if appt["conversation_id"] == owned and appt["status"] in ("booked", "confirmed"):
            booked_here.append(appt["id"])
    owned_lead = call("GET", f"/api/conversations/{owned}")["lead"]["id"]
    call("POST", f"/api/leads/{owned_lead}/assign", {"user_id": rep["id"]})

    say(owned, content="What's the out-the-door price on that?")
    check("a question a human must answer still stops the thread",
          call("GET", f"/api/conversations/{owned}")["status"] == "handoff")
    check("but on a buyer who has a rep it is born theirs, not the queue's",
          call("GET", f"/api/conversations/{owned}")["open_escalation"] is None)
    check("so assigning somebody does not have to be done twice",
          not any((e.get("lead") or {}).get("id") == owned_lead
                  for e in call("GET", "/api/overview")["queues"]["needs_a_person"]))
    check("and the lead row stops flying the flag as well",
          next(l for l in call("GET", "/api/leads?limit=500")["leads"]
               if l["id"] == owned_lead)["flagged"] is False)

    # Who works which lead is how a floor is run. A rep may say "I have this";
    # handing a buyer to somebody else -- or back to the pool -- is the
    # manager's call, and the menu hides what the server refuses.
    call("POST", "/api/auth/login", REP_LOGIN)
    marcus = call("GET", "/api/auth/me")["user"]["id"]
    other = next(m for m in call("GET", "/api/team")["members"] if m["id"] != marcus)
    denied, why = status_of("POST", f"/api/leads/{owned_lead}/assign", {"user_id": other["id"]})
    check("a rep cannot hand a buyer to somebody else", denied == 403, f"{denied} {why}"[:90])
    check("nor put one back in the pool",
          status_of("POST", f"/api/leads/{owned_lead}/assign", {"user_id": None})[0] == 403)
    check("but taking one themselves is still theirs to do",
          call("POST", f"/api/leads/{owned_lead}/assign",
               {"user_id": marcus})["assigned_to"]["id"] == marcus)
    call("POST", "/api/auth/login", LOGIN)
    check("and a manager still assigns anybody",
          call("POST", f"/api/leads/{owned_lead}/assign",
               {"user_id": other["id"]})["assigned_to"]["id"] == other["id"])

    print("\n== a buyer who changes their mind, and a visit that moves ==")
    # Naming a different car is a change of subject at any stage. The rails are
    # a state machine, so once the thread reached contact_capture "actually,
    # tell me about the X5" was read as an answer to "what is your email?" --
    # and the appointment was booked against the car they had moved off.
    counts: dict[str, int] = {}
    for vehicle in call("GET", "/api/inventory?status=available")["vehicles"]:
        counts[vehicle["model"]] = counts.get(vehicle["model"], 0) + 1
    named = [v for v in call("GET", "/api/inventory?status=available")["vehicles"]
             if counts[v["model"]] == 1][:2]
    check("the lot has two cars that name themselves unambiguously", len(named) == 2)
    first, second = named
    switch_tag = secrets.token_hex(4)
    switch_convo = call("POST", "/api/chat/sessions")["conversation_id"]
    # A fresh thread searches first and offers a shortlist -- that is the
    # opening move, and naming a car on turn one should not skip it.
    # Named in the opening search so it is really in the shortlist: the stub
    # can only focus on a car it has shown, which is the point of searching
    # first rather than taking the buyer's word for what is on the lot.
    say(switch_convo, content=f"Do you have a {first['make']} {first['model']}?")
    check("the opening turn searches rather than jumping to one car",
          call("GET", f"/api/conversations/{switch_convo}")["focus_vehicle_id"] is None)
    say(switch_convo, content=f"Tell me about the {first['make']} {first['model']}")
    check("the thread settles on the first car",
          call("GET", f"/api/conversations/{switch_convo}")["focus_vehicle_id"] == first["id"])
    say(switch_convo, content="Can I come in this week?")
    say(switch_convo, content=f"Actually, tell me about the {second['make']} {second['model']}")
    check("naming another car moves the focus, even past the booking questions",
          call("GET", f"/api/conversations/{switch_convo}")["focus_vehicle_id"] == second["id"],
          str(call("GET", f"/api/conversations/{switch_convo}")["focus_vehicle_id"]))
    # A car they already own is not a car they are asking to see. Without this
    # a buyer mentioning their trade-in gets re-focused onto whichever of ours
    # happens to share the model name.
    say(switch_convo, content=f"I'm trading in my old {first['make']} {first['model']}")
    check("but a car they are trading in is not a car they asked about",
          call("GET", f"/api/conversations/{switch_convo}")["focus_vehicle_id"] == second["id"],
          str(call("GET", f"/api/conversations/{switch_convo}")["focus_vehicle_id"]))

    # A visit can be moved without being destroyed. Cancel-and-rebook mints a
    # new row, so the appointment loses its id, its salesperson and the
    # outreach sent against it -- and the timeline shows a cancellation beside
    # a fresh booking rather than a move.
    times = call("GET", f"/api/conversations/{switch_convo}/availability")["days"]
    open_slots = [s["starts_at"] for day in times for s in day["slots"]]
    made = call("POST", f"/api/chat/sessions/{switch_convo}/book", {
        "starts_at": open_slots[0], "name": "Moves Around",
        "email": f"moves.{switch_tag}@example.invalid", "phone": "319-555-0177",
    })["appointment"]
    booked_here.append(made["id"])
    rep_id = next(m for m in call("GET", "/api/team")["members"] if m["role"] == "rep")["id"]
    call("POST", f"/api/appointments/{made['id']}/assign", {"user_id": rep_id})
    moved = call("POST", f"/api/appointments/{made['id']}/reschedule",
                 {"starts_at": open_slots[1]})
    check("an appointment can be moved and stays the same appointment",
          moved["id"] == made["id"] and moved["starts_at"].startswith(open_slots[1][:16]),
          moved["starts_at"])
    check("keeping the salesperson it was assigned to",
          (moved.get("assigned_to") or {}).get("id") == rep_id)
    check("and the time it left is offered again",
          open_slots[0] in [
              s["starts_at"]
              for day in call("GET", f"/api/conversations/{switch_convo}/availability")["days"]
              for s in day["slots"]
          ])
    check("a time somebody else holds is refused",
          status_of("POST", f"/api/appointments/{made['id']}/reschedule",
                    {"starts_at": open_slots[1]})[0] in {200, 409},
          "same time is a no-op, a taken one is a 409")

    # The card's idempotency key is deterministic on the slot, so a buyer who
    # booked a time, cancelled, and asked for it again matched the *cancelled*
    # row and got it handed back as already_booked. The card said booked, the
    # calendar showed a cancellation, and nothing had been booked at all.
    call("POST", f"/api/appointments/{made['id']}/cancel")
    retaken = call("POST", f"/api/chat/sessions/{switch_convo}/book", {
        "starts_at": open_slots[1], "name": "Moves Around",
        "email": f"moves.{switch_tag}@example.invalid", "phone": "319-555-0177",
    })["appointment"]
    booked_here.append(retaken["id"])
    check("re-booking a cancelled time makes a new appointment, not the dead one",
          retaken["id"] != made["id"] and retaken["status"] in {"booked", "confirmed"},
          f"{retaken['id'][:8]} vs {made['id'][:8]}, {retaken['status']}")

    print("\n== one fact, one answer, wherever it is read ==")
    # Everything here is the same shape of bug: a fact written in one row and
    # read from another, with nothing keeping the two in step. Each was found
    # by asking "what else could disagree like the queues did?" and each was
    # reproduced before it was fixed.

    # A call that ended while somebody was still owed a call back. Hanging up
    # is how most calls end, and it used to close the thread unconditionally --
    # so the row read Closed while sitting in Needs a person, which tells a
    # manager two opposite things at once. close_conversation had always been
    # careful about this; end_call was not.
    hung = call("POST", "/api/chat/sessions", {"channel": "voice"})["conversation_id"]
    call("POST", "/api/voice/tools", {
        "conversation_id": hung, "name": "escalate_to_human",
        "input": {"reason": "asked for an out-the-door price"}, "tool_call_id": f"otd-{hung}"})
    call("POST", f"/api/voice/sessions/{hung}/end", {})
    after_hangup = call("GET", f"/api/conversations/{hung}")
    check("a call the buyer hung up on does not close a thread a rep still owes",
          after_hangup["status"] == "handoff", after_hangup["status"])
    check("and it is still in Needs a person, saying the same thing the badge does",
          any(e["conversation_id"] == hung
              for e in call("GET", "/api/overview")["queues"]["needs_a_person"]))

    # A cancelled visit is not an appointment set. `stage` is written once by
    # book_appointment and nothing walked it back, so the conversation row kept
    # its green Appointment set badge -- and counted in the Appointed filter --
    # while the lead beside it derived the same thing from appointment rows and
    # correctly said there was none.
    cancelling = call("POST", "/api/chat/sessions", {})["conversation_id"]
    free = call("POST", "/api/voice/tools", {
        "conversation_id": cancelling, "name": "check_availability", "input": {},
        "tool_call_id": f"cav-{cancelling}"})["result"]["slots"]
    made = call("POST", "/api/voice/tools", {
        "conversation_id": cancelling, "name": "book_appointment",
        "input": {"name": "Cancel Probe", "email": "cancel.probe@example.invalid",
                  "starts_at": free[0]},
        "tool_call_id": f"cbk-{cancelling}"})["result"]
    check("booking puts the thread at the booked stage",
          call("GET", f"/api/conversations/{cancelling}")["stage"] == "booked")
    call("POST", f"/api/appointments/{made['appointment_id']}/cancel", {})
    thread_now = call("GET", f"/api/conversations/{cancelling}")
    lead_now = call("GET", f"/api/leads/{made['lead_id']}")
    check("cancelling walks it back, so the thread stops claiming an appointment",
          thread_now["stage"] != "booked", thread_now["stage"])
    check("and the thread and the buyer now answer Appointed the same way",
          (thread_now["stage"] == "booked") == (lead_now["stage"] == "appointment"),
          f"{thread_now['stage']} vs {lead_now['stage']}")

    # A rep who leaves, still holding buyers. They drop off the roster and
    # their leads stay pointing at them -- not unclaimed, so no queue asks
    # anyone to pick them up, and not workable, because the owner is gone and
    # cannot be chosen from the assign menu. The work appears nowhere at all.
    leaver = next(m for m in call("GET", "/api/team")["members"] if m["role"] == "rep")
    handful = [l for l in call("GET", "/api/leads")["leads"][:40]][:2]
    for lead in handful:
        call("POST", f"/api/leads/{lead['id']}/assign", {"user_id": leaver["id"]})
    left = call("PATCH", f"/api/team/{leaver['id']}", {"active": False})
    check("somebody leaving hands their buyers back rather than taking them along",
          left["leads_returned"] >= len(handful), str(left)[:90])
    queue = {l["id"] for l in call("GET", "/api/overview")["queues"]["unclaimed_leads"]}
    check("so those buyers are in a queue somebody is actually looking at",
          all(lead["id"] in queue for lead in handful),
          f"{sum(1 for lead in handful if lead['id'] in queue)}/{len(handful)}")
    # Un-assigned, never cancelled. A visit still happening with nobody to host
    # it belongs in the unassigned queue; deleting it because a rep left would
    # be a far worse answer.
    check("and their future visits are handed back, not cancelled",
          "appointments_returned" in left and "escalations_reopened" in left, str(left)[:90])
    call("PATCH", f"/api/team/{leaver['id']}", {"active": True})
    check("and they are back on the roster when they return",
          any(m["id"] == leaver["id"] for m in call("GET", "/api/team")["members"]))

    print("\n== the marketing site books a demo ==")
    stamp = secrets.token_hex(4)
    # The page's own back end. Its customer is a dealership rather than a car
    # buyer, which is why it has its own table: putting prospects into `leads`
    # would put strangers in the list a rep works from.
    offer = call("GET", "/api/demo/slots")
    check("the site offers times somebody can really book",
          bool(offer["days"]) and all(d["slots"] for d in offer["days"]),
          f"{len(offer['days'])} days")
    check("and names the timezone they are in",
          bool(offer["timezone"]), offer["timezone"])
    # The wording comes from the server so the checkbox and the row it writes
    # cannot disagree about what somebody agreed to.
    check("with the consent wording served, not hardcoded in the page",
          "Reply STOP to opt out" in offer["consent_text"], offer["consent_text"][:50])

    first = offer["days"][0]
    when = f"{first['date']}T{first['slots'][0]}"
    payload = {
        "name": "Smoke Prospect", "dealership": "Test Motors",
        "email": f"prospect.{stamp}@example.invalid", "phone": "319-555-0111",
        "dealership_url": "https://testmotors.example", "slot": when, "consent": True,
    }
    booked_demo = call("POST", "/api/demo/requests", payload)
    check("a demo can be booked from the page", booked_demo["kind"] == "demo",
          str(booked_demo)[:70])
    check("and it answers with a person to reply to",
          "@" in (booked_demo.get("reply_to") or ""), str(booked_demo.get("reply_to")))
    # Re-decided at submit, not at render: the form sits on screen while
    # somebody types their details, so "still open" a minute ago is not an
    # answer. Same rule book_appointment follows for a buyer.
    check("the same time cannot be taken twice",
          status_of("POST", "/api/demo/requests", payload)[0] == 409)
    check("and it stops being offered",
          when.split("T")[1] not in
          next((d["slots"] for d in call("GET", "/api/demo/slots")["days"]
                if d["date"] == first["date"]), []),
          f"{when} still on offer")

    # The tick is the record. Without it the row could not say whether anyone
    # agreed to be contacted, which is the only thing that row is for.
    check("a booking with no consent is refused",
          status_of("POST", "/api/demo/requests", {**payload, "consent": False,
                                                   "slot": None})[0] == 400)
    check("and so is one with no way to reach them",
          status_of("POST", "/api/demo/requests",
                    {"name": "No Contact", "consent": True})[0] == 400)

    # Support has no slot, and that is the only difference.
    helped = call("POST", "/api/demo/requests", {
        "name": "Curious GM", "email": f"gm.{stamp}@example.invalid",
        "message": "Does this work with two rooftops?", "consent": True,
    })
    check("a support message needs no calendar", helped["kind"] == "support",
          str(helped)[:60])
    # And agrees to the right thing. Somebody reporting a fault is not booking
    # a demo, and the support form takes no phone number -- so a consent record
    # promising phone, text and an SMS opt-out describes something that did not
    # happen, which is the one thing a consent record is for.
    check("the site offers a separate wording for a message, not the demo one",
          offer["support_consent_text"] != offer["consent_text"]
          and "demo" not in offer["support_consent_text"].lower(),
          offer.get("support_consent_text", "")[:70])
    # Read straight from the row: what was shown and what was stored have to
    # be the same words, and the endpoint that would serve it is ours rather
    # than the dealership's, so this is the honest way to ask from here.
    from app.db import SessionLocal as _S
    from app.models import DemoRequest as _DR
    with _S() as _db:
        stored = _db.query(_DR).filter_by(id=helped["id"]).one().consent_text
    check("and it is the wording stored on the row, not the demo one",
          stored == offer["support_consent_text"], stored[:70])
    # Ours, not the dealership's. These are other dealerships asking us for a
    # demo -- a list of Riverside Auto's competitors, which is about the last
    # thing their staff should read from inside their own dashboard.
    check("but reading them back takes a session at all",
          anonymous_status("/api/demo/requests") == 401)
    check("and a dealership's manager is refused",
          status_of("GET", "/api/demo/requests")[0] == 403)
    # Given back, like every other slot this script takes. The demo calendar
    # rolls forward so this one would heal on its own, but a run that leaves
    # bookings behind is the habit that cost thirty-six stranded appointments.
    # Cancelling is ours now, so it takes an ops session and hands the
    # dealership's back afterwards -- one cookie jar, one session at a time.
    call("POST", "/api/auth/login",
         {"email": "founder@linerai.us", "password": "liner-dev"})
    released = call("POST", f"/api/demo/requests/{booked_demo['id']}/cancel")
    call("POST", "/api/auth/login", LOGIN)
    check("and cancelling gives the time back to the page",
          released["cancelled"] is True
          and when.split("T")[1] in next(
              (d["slots"] for d in call("GET", "/api/demo/slots")["days"]
               if d["date"] == first["date"]), []),
          when)

    print("\n== guessing a password gets slower ==")
    # The login form is public on a public host, and the password is the only
    # thing in front of a dealership's buyer list and of /ops. Nothing used to
    # slow a guess down or record that one was happening.
    from app.api.auth import attempts as login_attempts
    from app.config import settings as cfg

    victim = f"bruteforce.{stamp}@example.invalid"
    codes = [
        status_of("POST", "/api/auth/login", {"email": victim, "password": "nope"})[0]
        for _ in range(cfg.login_max_attempts + 2)
    ]
    check("a wrong password is refused, then the attempts run out",
          codes[: cfg.login_max_attempts] == [401] * cfg.login_max_attempts
          and codes[cfg.login_max_attempts:] == [429, 429],
          str(codes))
    blocked, body = status_of("POST", "/api/auth/login",
                              {"email": victim, "password": "nope"})
    check("and it says when to come back rather than just refusing",
          blocked == 429 and "seconds" in body, body[:70])
    # An unknown address is limited exactly like a real one -- a limit that
    # only bites on accounts that exist is an enumeration oracle, which is a
    # worse leak than the one it guards.
    check("an address nobody owns is refused the same way",
          status_of("POST", "/api/auth/login",
                    {"email": victim, "password": "x"})[0] == 429)

    # And takes the same *time*. Skipping bcrypt when the address matches
    # nobody returned in about 2ms against about 265ms for a real account --
    # so "is founder@ an account here?" was answerable with a stopwatch,
    # whatever the status code and the message said. Measured at 262.9ms
    # before the fix and 0.3ms after.
    import statistics
    import time as _time

    def _timed(email: str) -> float:
        start = _time.perf_counter()
        status_of("POST", "/api/auth/login", {"email": email, "password": "wrong-one"})
        return (_time.perf_counter() - start) * 1000

    unknown = [_timed(f"ghost.{stamp}.{i}@example.invalid") for i in range(7)]
    real = []
    for _ in range(7):
        call("POST", "/api/auth/login", LOGIN)      # keep the counter clear
        real.append(_timed(LOGIN["email"]))
    quick, slow = statistics.median(unknown), statistics.median(real)
    # A ratio, not a millisecond count: this runs on whatever machine it runs
    # on. Under 2x is indistinguishable; the bug was a hundredfold.
    check("and takes the same time, so a stopwatch cannot name the accounts",
          max(quick, slow) / max(min(quick, slow), 0.001) < 2.0,
          f"unknown {quick:.0f}ms vs real {slow:.0f}ms")

    # Keyed on the account, not the caller. Behind a proxy every request looks
    # like one IP, so an address key is what stops one bot locking out the
    # whole company.
    check("but spraying one account does not lock anybody else out",
          call("POST", "/api/auth/login", LOGIN)["user"]["role"] == "manager")

    # Somebody who mistyped four times and then got it right must not still be
    # four attempts from a lockout for the rest of the window.
    for _ in range(cfg.login_max_attempts - 1):
        status_of("POST", "/api/auth/login", {"email": LOGIN["email"], "password": "no"})
    call("POST", "/api/auth/login", LOGIN)
    check("and a correct password clears the count it took to get there",
          status_of("POST", "/api/auth/login",
                    {"email": LOGIN["email"], "password": "no"})[0] == 401)
    # Leave the gate's own account clean for everything after this.
    login_attempts.clear(LOGIN["email"])
    call("POST", "/api/auth/login", LOGIN)

    print("\n== our own dashboard is ours ==")
    # /ops is Liner's, not a dealership's. The separation is a third role
    # rather than a senior manager: a manager runs a showroom and has every
    # reason to read its buyer list, which is exactly what these two do not.
    check("a dealership's manager cannot reach it",
          status_of("GET", "/api/ops/summary")[0] == 403)
    check("and nor can a stranger", anonymous_status("/api/ops/summary") == 401)

    call("POST", "/api/auth/login",
         {"email": "founder@linerai.us", "password": "liner-dev"})
    check("but Liner's own account can",
          call("GET", "/api/ops/summary")["unread"] >= 0)
    # And the wall runs the other way too, at the session rather than at each
    # endpoint: two tables mean a uid from one is meaningless in the other.
    check("while an ops session is refused by the dealership's API",
          status_of("GET", "/api/overview")[0] == 403)
    check("and by its buyer list", status_of("GET", "/api/leads")[0] == 403)
    check("but who-am-I still answers, or neither dashboard could load",
          call("GET", "/api/auth/me")["user"]["role"] == "owner")

    before = call("GET", "/api/ops/summary")["unread"]
    slots = call("GET", "/api/demo/slots")["days"]
    day = next(d for d in slots if d["slots"])
    ops_demo = call("POST", "/api/demo/requests", {
        "name": "Ops Prospect", "dealership": "Ops Motors",
        "email": f"ops.{stamp}@example.invalid", "phone": "319-555-0112",
        "slot": f"{day['date']}T{day['slots'][0]}", "consent": True,
    })
    check("a booking arrives unread", call("GET", "/api/ops/summary")["unread"] == before + 1,
          f"{before} -> {call('GET', '/api/ops/summary')['unread']}")
    # Opening one is what clears it, and it stays cleared -- a notification
    # that survives being read is one people stop looking at. Deliberately a
    # state on the row, not a per-person receipt: there are two of us and "I
    # have seen it" from either is the answer the other needs.
    seen = call("POST", f"/api/ops/demos/{ops_demo['id']}/status", {"status": "seen"})
    check("opening it clears the notification", seen["unread"] is False, str(seen)[:60])
    check("and the count comes back down",
          call("GET", "/api/ops/summary")["unread"] == before)
    check("marking it read twice changes nothing",
          call("POST", f"/api/ops/demos/{ops_demo['id']}/status",
               {"status": "seen"})["unread"] is False)
    check("an invented status is refused",
          status_of("POST", f"/api/ops/demos/{ops_demo['id']}/status",
                    {"status": "archived"})[0] == 400)

    entry = call("GET", f"/api/ops/demos/{ops_demo['id']}")
    check("the entry carries the wording they agreed to, not just that they did",
          "Reply STOP to opt out" in (entry["consent_text"] or "")
          and bool(entry["consented_at"]), str(entry.get("consent_text"))[:40])

    box = call("GET", "/api/ops/mail")
    check("the inbox counts what it shows",
          box["counts"]["all"] == len(box["messages"]), str(box["counts"]))
    for name in ("demos", "support", "unmatched", "unread"):
        one = call("GET", f"/api/ops/mail?box={name}")
        # Two copies of a box's predicate is how a tab says 12 and shows 9.
        check(f"the {name} box agrees with its own count",
              one["counts"][name] == len(one["messages"]),
              f"{one['counts'][name]} vs {len(one['messages'])}")
    check("an unknown box is a 400, not a silent 'all'",
          status_of("GET", "/api/ops/mail?box=spam")[0] == 400)

    reply = call("POST", "/api/ops/mail/reply", {
        "to": f"ops.{stamp}@example.invalid", "subject": "Re: your demo",
        "body": "See you then.",
    })
    # Straight through the same sender and the same outbound limit as a
    # dealer's composer -- a reply typed to a real prospect from a rehearsal
    # is exactly what that check exists to stop.
    check("a reply reports the provider that handled it rather than a green tick",
          bool(reply.get("provider")) and bool(reply.get("detail") or reply.get("reason")),
          str(reply)[:90])
    # Two people share this inbox. The `From` is the deployment's one verified
    # sender, but the return path is whoever pressed send -- a reply that
    # always came back to the founder sent half the answers to the wrong one.
    check("and comes back to whoever sent it, not to a fixed address",
          reply.get("reply_to") == "founder@linerai.us", str(reply.get("reply_to")))
    call("POST", "/api/auth/login",
         {"email": "cto@linerai.us", "password": "liner-dev"})
    check("so the other account's replies come back to them",
          call("GET", "/api/ops/summary")["reply_to"] == "cto@linerai.us",
          call("GET", "/api/ops/summary")["reply_to"])
    check("and the composer is told the same address the send will use",
          call("POST", "/api/ops/mail/reply",
               {"to": f"ops.{stamp}@example.invalid", "subject": "Re: your demo",
                "body": "Confirming."})["reply_to"]
          == call("GET", "/api/ops/summary")["reply_to"])
    check("and an address that is not one is refused",
          status_of("POST", "/api/ops/mail/reply",
                    {"to": "nobody", "subject": "x", "body": "y"})[0] == 400)

    # One handler for both, because reaching a dealership we want to talk to
    # is the same act as answering one that wrote in -- and a second endpoint
    # for it is how one of the two stops going through blocked_reason.
    cold = call("POST", "/api/ops/mail/reply", {
        "to": f"never.wrote.{stamp}@example.invalid",
        "subject": "About Liner", "body": "Reaching out about a demo.",
    })
    check("a first message to somebody who never wrote in goes the same way",
          bool(cold.get("provider")) and cold.get("reply_to") == "cto@linerai.us",
          str(cold)[:80])

    # Sending used to leave no row at all, so there was no Sent box to have
    # and no way to answer "did I already write to them?" the next morning.
    sent_box = call("GET", "/api/ops/mail?box=sent")
    check("and it is kept, so Sent is a real box rather than a label",
          any(m["id"] == cold["message_id"] for m in sent_box["messages"]),
          f"{sent_box['counts']['sent']} in Sent")

    # A draft is the same row before it goes. Copying it across tables on send
    # is how one message a person wrote becomes two rows in two boxes.
    draft = call("POST", "/api/ops/mail/draft", {
        "to": f"draft.{stamp}@example.invalid", "subject": "Half written",
        "body": "Will finish this later.",
    })
    drafts = call("GET", "/api/ops/mail?box=drafts")
    check("a half-written message is kept on the server, not in the tab",
          any(m["id"] == draft["id"] for m in drafts["messages"]), str(drafts["counts"]))
    check("an empty one is refused, so Drafts does not fill with ghosts",
          status_of("POST", "/api/ops/mail/draft", {"to": "", "subject": "", "body": ""})[0]
          == 400)
    before = call("GET", "/api/ops/mail")["counts"]
    call("POST", "/api/ops/mail/send", {
        "draft_id": draft["id"], "to": f"draft.{stamp}@example.invalid",
        "subject": "Half written", "body": "Finished it.",
    })
    after = call("GET", "/api/ops/mail")["counts"]
    check("sending a draft moves that row rather than minting a second",
          after["drafts"] == before["drafts"] - 1 and after["sent"] == before["sent"] + 1,
          f"{before['drafts']}/{before['sent']} -> {after['drafts']}/{after['sent']}")

    # Unresolved deliveries used to be hardcoded read, so the one box holding
    # mail from strangers could never say which of it was new.
    # Delivered here rather than picked off the top of the box: an earlier run
    # of `make ops-ui` reads one, and a check that only holds on a database
    # nobody has touched is a check that fails for the wrong reason later.
    fresh_id = f"<ops-unread-{stamp}>"
    # The subject carries the run stamp, and that is the whole point of it.
    # It used to be a fixed string, so the poll below matched a row an *earlier*
    # run had left in Unmatched -- and the check passed while this run's
    # delivery had gone somewhere else entirely. Which is exactly what it did:
    # a stranger writing to support@ was being minted as a car buyer on the
    # dealership's list, and this assertion sailed straight over it.
    fresh_subject = f"Is this thing on? {stamp}"
    inbound({
        "messageId": fresh_id, "from": f"stranger.{stamp}@nowhere.invalid",
        "to": "support@example.invalid", "subject": fresh_subject,
        "text": "Writing in cold.",
    })
    # Polled on the ops mailbox rather than with settled(), which reads
    # /api/email/receipts -- a dealership endpoint, and an ops session is
    # refused by every one of those exactly as a rep is refused by /api/ops.
    stranger: list[dict] = []
    for _ in range(40):
        stranger = [
            m for m in call("GET", "/api/ops/mail?box=unmatched")["messages"]
            if m["subject"] == fresh_subject
        ]
        if stranger:
            break
        time.sleep(0.25)
    check("a delivery that resolved to nobody reaches the ops mailbox",
          bool(stranger), "it should be in Unmatched")
    if stranger:
        first = stranger[0]
        check("mail from a stranger arrives unread", first["unread"], str(first["unread"]))
        call("POST", "/api/ops/mail/read",
             {"kind": "email", "id": first["id"], "read": True})
        check("reading it clears it",
              not next(m for m in call("GET", "/api/ops/mail?box=unmatched")["messages"]
                       if m["id"] == first["id"])["unread"])
        call("POST", "/api/ops/mail/read",
             {"kind": "email", "id": first["id"], "read": False})
        check("and it can be put back, which is what makes an inbox a queue",
              next(m for m in call("GET", "/api/ops/mail?box=unmatched")["messages"]
                   if m["id"] == first["id"])["unread"])

        # Trash is a timestamp. A message somebody wrote is the last thing to
        # destroy on their behalf.
        call("POST", "/api/ops/mail/trash",
             {"kind": "email", "id": first["id"], "trashed": True})
        binned = call("GET", "/api/ops/mail?box=trash")
        check("trashing takes it out of every box but Trash",
              any(m["id"] == first["id"] for m in binned["messages"])
              and not any(m["id"] == first["id"]
                          for m in call("GET", "/api/ops/mail")["messages"]))
        call("POST", "/api/ops/mail/trash",
             {"kind": "email", "id": first["id"], "trashed": False})
        check("and restoring puts it back where it was",
              any(m["id"] == first["id"]
                  for m in call("GET", "/api/ops/mail?box=unmatched")["messages"]))

    check("an unknown box is refused rather than answered with the default",
          status_of("GET", "/api/ops/mail?box=archive")[0] == 400)

    call("POST", f"/api/demo/requests/{ops_demo['id']}/cancel")
    # Back to the dealership for everything after this -- including the
    # slot-release `finally`, which reads /api/appointments. One jar, one
    # session at a time, and leaving an ops session behind here made the
    # teardown 403 and every run leak its bookings.
    call("POST", "/api/auth/login", LOGIN)
    check("and signing back in as the dealership still works",
          call("GET", "/api/auth/me")["user"]["role"] == "manager")

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
    # Waited for, not slept through. It was a flat 1.5 seconds, which is fine
    # on a fresh seed and progressively less fine as the events table grows --
    # the socket replays its backlog on connect, so a database several runs old
    # takes longer to reach the frames this asserts. It failed twice in a row
    # on an accumulated database while the code under test was correct, which
    # is the worst kind of check: it sends you hunting a bug that is not there.
    wanted = ("appointment.booked", "appointment.confirmed", "appointment.assigned",
              "outreach.sent", "handoff.triggered", "lead.imported")
    for _ in range(60):
        if all(event in listener.seen for event in wanted):
            break
        time.sleep(0.25)
    check("socket connected", not listener.error, listener.error or "ok")
    for event in wanted:
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
        # The strip counts conversations, not turns. It used to count entries,
        # so one eight-minute call with sixteen transcript lines read
        # `Voice call 17` -- directly under a header saying `1 thread`. Nobody
        # reads that as lines of transcript; it says seventeen phone calls to
        # the manager deciding whether this buyer has been chased enough.
        threads = {}
        for entry in mixed["entries"]:
            if entry["kind"] in {"message", "call"} and entry["channel"]:
                threads.setdefault(entry["channel"], set()).add(entry["conversation_id"])
        for name, ids in threads.items():
            check(f"the {name} tab counts conversations, not turns",
                  mixed["channels"][name] == len(ids),
                  f"says {mixed['channels'][name]}, has {len(ids)}")
        spoken = [e for e in mixed["entries"] if e["channel"] == "voice"]
        check("so a call with a transcript still counts as one call",
              len(spoken) > mixed["channels"]["voice"],
              f"{len(spoken)} voice entries, {mixed['channels']['voice']} on the tab")
        # Each email is its own contact: there is no thread to fold them into,
        # and two emails on one day are two times we wrote to somebody.
        emails = [e for e in mixed["entries"] if e["channel"] == "email"]
        if emails:
            check("while every email counts on its own",
                  mixed["channels"]["email"] == len(emails),
                  f"{mixed['channels']['email']} vs {len(emails)}")
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

    # ...and the other half of the ladder, which was missing. Resolution ran
    # once, at delivery, and never again -- so somebody who wrote to sales@
    # before they were anybody stayed a stranger for good, even after they
    # chatted and booked with that same address the next day. One person, two
    # records, and nothing that would ever join them.
    early = f"early.{run}@example.invalid"
    inbound({
        "messageId": f"<smoke-{run}-early>", "from": early,
        "to": "sales@example.invalid", "subject": "Is the Sienna still there?",
        "text": "Saw it on your site.",
    })
    check("mail from a buyer we do not know yet mints one, then and there",
          settled(f"<smoke-{run}-early>").get("matched_by") == "new_lead",
          str(settled(f"<smoke-{run}-early>").get("matched_by")))
    later = call("POST", "/api/chat/sessions", {})["conversation_id"]
    when = call("POST", "/api/voice/tools", {
        "conversation_id": later, "name": "check_availability", "input": {},
        "tool_call_id": f"eav-{run}"})["result"]["slots"]
    minted = call("POST", "/api/voice/tools", {
        "conversation_id": later, "name": "book_appointment",
        "input": {"name": "Early Writer", "email": early, "starts_at": when[0]},
        "tool_call_id": f"ebk-{run}"})["result"]
    joined = settled(f"<smoke-{run}-early>")
    check("and booking later is that same buyer, never a second one",
          joined.get("outcome") == "accepted" and joined.get("lead_id") == minted["lead_id"],
          f"{joined.get('outcome')} -> {joined.get('lead_id')}")
    check("landing on their timeline, not on the unmatched pile",
          any("Sienna still there" in json.dumps(e)
              for e in call("GET", f"/api/leads/{minted['lead_id']}/timeline")["entries"]))
    # Through the same resolution as the live path, not a second copy of it --
    # two ladders is how they start disagreeing about who a reply belongs to.
    check("by the same rule the live path uses, which is what it records",
          joined.get("matched_by") == "new_lead", str(joined.get("matched_by")))

    # The retroactive half still has to run, and now it is reached by the
    # deliveries a lead is deliberately *not* minted from. A robot's message
    # sits unresolved; if that same mailbox later turns out to be a buyer, the
    # ladder is re-run rather than the message being lost.
    quiet = f"quiet.{run}@example.invalid"
    inbound({
        "messageId": f"<smoke-{run}-quiet>", "from": quiet,
        "to": "sales@example.invalid", "subject": "(nothing)", "text": "   ",
    })
    check("a delivery no lead was minted from stays a receipt",
          settled(f"<smoke-{run}-quiet>").get("outcome") == "unresolved")
    made_later = call("POST", "/api/leads", {
        "name": "Quiet Writer", "email": quiet, "phone": "", "source": "manual",
    })
    picked = settled(f"<smoke-{run}-quiet>")
    check("and is placed the moment that buyer comes into existence",
          picked.get("outcome") == "accepted"
          and picked.get("lead_id") == made_later["lead"]["id"],
          f"{picked.get('outcome')} -> {picked.get('lead_id')}")
    # Given back, like every other appointment this script books. The fixture's
    # week holds about twenty slots and book_appointment refuses a clash, so a
    # run that kept one leaves fewer for the next -- and after enough runs the
    # booking flow fails with "0 times on the card" somewhere unrelated. That
    # has happened before; this section caused it again.
    call("POST", f"/api/appointments/{minted['appointment_id']}/cancel", {})

    # No session guards this endpoint, so an unbounded body is a firehose
    # anyone who finds the URL can point at it. The limit matches the Worker's.
    oversized = inbound({"messageId": f"<smoke-{run}-big>", "text": "x" * (11 * 1024 * 1024)})
    check("an oversized delivery is refused before it is parsed",
          oversized[0] == 413, str(oversized[0]))

    print("\n== the shape a real reply actually arrives in ==")
    # Taken from a wrangler tail of the deployed Worker, not invented: it names
    # the token `conversationId`, quotes the whole original message back, sends
    # Outlook's HTML, and carries an SES message id in In-Reply-To.
    real = {
        "messageId": f"<MW4PR20MB5131{run}@MW4PR20MB5131.namprd20.prod.outlook.com>",
        "from": send["to_address"],
        "to": f"reply+{send['reply_token']}@linerai.us",
        "conversationId": send["reply_token"],
        "subject": "Re: Liner test message",
        "fromAddress": send["to_address"],
        "fromName": "A Buyer",
        "text": (
            "what\n________________________________\n"
            "From: Riverside Auto <support@linerai.us>\nSent: Wednesday\n"
            "To: buyer\nSubject: Liner test message\n\n\n"
            "This is a test from the Liner dashboard.\n"
        ),
        "html": "<html><head><style>P{}</style></head><body><div>what</div></body></html>",
        "inReplyTo": "<0100019ff27db392-0f8f4780@email.amazonses.com>",
        "attachments": [],
    }
    inbound(real)
    landed = settled(real["messageId"])
    check("a real Outlook reply resolves by its token", landed.get("matched_by") == "reply_token",
          f"{landed.get('outcome')} / {landed.get('matched_by')}")

    body = next(
        (m["body"] for m in call("GET", "/api/email/messages?box=received")["messages"]
         if m["lead_id"] == send["lead_id"]),
        "",
    )
    # Every reply carries our own last message back. Storing that means a rep
    # reads one word followed by four paragraphs they wrote themselves, and
    # the reply after it carries two copies.
    check("and is stored as what the buyer wrote, not the thread they quoted",
          "Riverside Auto" not in body, repr(body[:60]))

    # The Worker names the token `conversationId`, so reading it means the
    # deployed Worker needs no edit at all.
    hinted = dict(real)
    hinted["messageId"] = f"<hint-{run}>"
    hinted["to"] = "support@linerai.us"
    inbound(hinted)
    by_hint = settled(hinted["messageId"])
    check("the token is read from the payload too, not only from the address",
          by_hint.get("matched_by") == "reply_token", str(by_hint.get("matched_by")))

    print("\n== the exact payload the deployed worker builds ==")
    # Field for field what integrations/email/worker/src/index.ts sends, so a
    # change on this side that the Worker cannot survive fails here rather
    # than in production. It is not a hypothetical shape: `inReplyTo: null`
    # once made every unthreaded reply a 400, and because a sensible Worker
    # reads 4xx as "my payload is wrong, retrying will not help", those
    # replies were lost rather than delayed.
    def worker_payload(**over: object) -> dict:
        body = {
            "messageId": f"<worker-{run}-1@outlook.com>",
            "from": send["to_address"],
            "to": f"reply+{send['reply_token']}@linerai.us",
            "conversationId": send["reply_token"],
            "subject": "Re: Liner test message",
            "fromAddress": send["to_address"],
            "fromName": "A Buyer",
            "text": "sounds good",
            "html": "<div>sounds good</div>",
            # Null, not absent and not "". `parsed.inReplyTo ?? null` is the
            # obvious way to write "there wasn't one", so the schema must not
            # be stricter than the wire.
            "inReplyTo": None,
            "references": None,
            "date": "2026-08-11T10:00:00Z",
            "receivedAt": "2026-08-11T10:00:01.123Z",
            "attachments": [],
        }
        body.update(over)
        return body

    inbound(worker_payload(), path="/api/emails/inbound", shared=WEBHOOK_SECRET.decode())
    landed = settled(f"<worker-{run}-1@outlook.com>")
    check("a null inReplyTo is a missing header, not a malformed payload",
          landed.get("outcome") == "accepted", str(landed.get("outcome")))
    check("and the fields the worker sends that we do not read are tolerated",
          landed.get("matched_by") == "reply_token", str(landed.get("matched_by")))

    # The Worker retries a 5xx or a dropped connection. Re-posting the same
    # bytes must cost a buyer nothing.
    twice = worker_payload(messageId=f"<worker-{run}-2@outlook.com>")
    inbound(twice, path="/api/emails/inbound", shared=WEBHOOK_SECRET.decode())
    again = inbound(twice, path="/api/emails/inbound", shared=WEBHOOK_SECRET.decode())
    check("its retry is recognised rather than filed a second time",
          isinstance(again[1], dict) and again[1].get("outcome") == "duplicate",
          str(again[1]))

    # Not every message carries a Message-ID header, and JSON.stringify drops
    # the key entirely when postal-mime found none. Without a fallback the
    # dedupe above has nothing to key on and the retry files twice.
    headerless = worker_payload()
    headerless.pop("messageId")
    first = inbound(headerless, path="/api/emails/inbound", shared=WEBHOOK_SECRET.decode())
    repeat = inbound(headerless, path="/api/emails/inbound", shared=WEBHOOK_SECRET.decode())
    check("mail with no Message-ID is still accepted",
          isinstance(first[1], dict) and first[1].get("outcome") == "received",
          str(first[1]))
    check("and its retry is caught on the bytes instead",
          isinstance(repeat[1], dict) and repeat[1].get("outcome") == "duplicate",
          str(repeat[1]))
    # The other half of that trade, and the half that would be invisible if it
    # broke: two real emails must stay two. The Worker stamps receivedAt once
    # per invocation at millisecond resolution, so a buyer writing the same
    # word twice still produces two different bodies.
    second = worker_payload(receivedAt="2026-08-11T10:00:09.987Z")
    second.pop("messageId")
    landed_second = inbound(second, path="/api/emails/inbound",
                            shared=WEBHOOK_SECRET.decode())[1]
    check("while a genuinely second message is not mistaken for a retry",
          isinstance(landed_second, dict) and landed_second.get("outcome") == "received",
          str(landed_second))

    # A synthetic id is a dedupe key and nothing else. Echoed back out as an
    # In-Reply-To it would name a message that never existed.
    landed_ids = [
        e.get("provider_message_id") or ""
        for e in call("GET", f"/api/leads/{send['lead_id']}/timeline")["entries"]
    ]
    check("and the id we invented never leaves the building",
          not any(i.startswith("sha256:") for i in landed_ids),
          str([i for i in landed_ids if i.startswith("sha256:")][:2]))

    # The catch-all also carries mail to support@ and sales@, where there is no
    # token at all and the Worker sends conversationId: null.
    stranger_id = f"<worker-{run}-cold@outlook.com>"
    inbound(
        worker_payload(messageId=stranger_id, to="sales@linerai.us", conversationId=None),
        path="/api/emails/inbound", shared=WEBHOOK_SECRET.decode(),
    )
    cold = settled(stranger_id)
    check("a null conversationId is a message with no token, not a broken one",
          cold.get("outcome") in {"accepted", "unresolved"}, str(cold.get("outcome")))

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

    reply_lead = call("GET", f"/api/conversations/{mine}")["lead"]["id"]
    me_id = call("GET", "/api/auth/me")["user"]["id"]

    inbound({
        "messageId": f"<smoke-{run}-reopen>", "from": escalation_email,
        "to": "sales@example.invalid", "subject": "Re:",
        "text": "Following up on my question",
    })
    reopened = settled(f"<smoke-{run}-reopen>")
    check("the reply reaches the buyer it came from",
          reopened.get("outcome") == "accepted", str(reopened.get("outcome")))
    # Their turn became ours again -- and "ours" is the rep who took them over,
    # not the unclaimed pool. A buyer with somebody on them does not need a
    # person to be *found*, which is the only question Needs a person asks, so
    # putting them there is how a manager assigns somebody already assigned.
    owner_now = call("GET", f"/api/conversations/{mine}")["open_escalation"]
    check("a reply on an owned buyer does not ask for a person again",
          owner_now is None, str(owner_now))
    check("it goes back to the rep who owns them",
          any(e["kind"] == "escalation" and (e.get("claimed_by") or {}).get("id") == me_id
              for e in call("GET", f"/api/leads/{reply_lead}/timeline")["entries"]))

    # And the other half of the same rule: with nobody on the buyer, the reply
    # really does raise a person, because now there is nobody to raise it to.
    call("POST", f"/api/conversations/{mine}/handback")
    call("POST", f"/api/leads/{reply_lead}/assign", {"user_id": None})
    inbound({
        "messageId": f"<smoke-{run}-reopen-loose>", "from": escalation_email,
        "to": "sales@example.invalid", "subject": "Re:",
        "text": "Still waiting on that number",
    })
    settled(f"<smoke-{run}-reopen-loose>")
    check("with nobody on the buyer, the same reply does ask for a person",
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
        page = call("GET", f"/api/email/messages?box={name}")
        # `matching`, not `len(messages)`. The list is a page; the tab is the
        # total. Comparing the tab to the page is how this read "says 230,
        # shows 200" once a box outgrew one screenful -- the same
        # two-definitions bug `_in_box` exists to prevent, arriving through a
        # silent cap instead of a second ternary.
        check(f"the {name} tab counts what the filter matches",
              page["matching"] == counts[name],
              f"says {counts[name]}, matches {page['matching']}")
        check(f"and the {name} page never claims to be the whole list",
              len(page["messages"]) <= page["matching"]
              and page["has_more"] == (len(page["messages"]) < page["matching"]),
              f"{len(page['messages'])} of {page['matching']}, has_more={page['has_more']}")

    # Paging grows the window, so a bigger limit is a superset -- not a
    # different slice of a re-sorted list.
    small = call("GET", "/api/email/messages?box=all&limit=5")
    bigger = call("GET", "/api/email/messages?box=all&limit=20")
    check("a page is the newest N, and asking for more extends it",
          len(small["messages"]) <= 5
          and [m["id"] for m in small["messages"]]
          == [m["id"] for m in bigger["messages"]][:len(small["messages"])],
          f"{len(small['messages'])} then {len(bigger['messages'])}")

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

    print("\n== the summary a buyer keeps is built from rows ==")
    # `close_conversation` takes a model-written `summary` and that used to be
    # the whole email. A real call mailed: "John Doe is all set with an
    # appointment tomorrow at 11 AM ... A summary will be sent to
    # john@outlook.com" -- a status line about the reader, in the third person,
    # telling them the thing they are holding is on its way to them.
    booked = next(
        (l for l in call("GET", "/api/leads")["leads"]
         if l.get("email") and l.get("appointment_count")),
        None,
    ) or next(l for l in call("GET", "/api/leads")["leads"] if l.get("email"))
    kept = call("GET", f"/api/leads/{booked['id']}/summary-preview")
    body = kept["body"]
    check("it greets the buyer rather than describing them",
          body.startswith("Hi"), body[:40])
    check("and never announces itself as something about to be sent",
          "will be sent" not in body.lower() and "summary will" not in body.lower(),
          body[:80])
    # Every line has to be checkable against a row, which is the whole reason
    # it is composed rather than written.
    check("it names the dealership a buyer can ring back",
          call("GET", "/api/overview")["dealership"]["phone"] in body)
    if kept["appointment"]:
        check("and states the appointment rather than alluding to it",
              "Your appointment:" in body, body[-200:])

    print("\n== a manager can write one, not only read them ==")
    # A composer that could only reach buyers already on file would send a rep
    # back to their own mail client to answer a stranger -- where the reply is
    # invisible to this system for good. So it takes any address, and says
    # which of the two happened rather than restricting.
    known = call("GET", "/api/email/recipients")["recipients"]
    check("the composer can offer buyers who have an address", bool(known),
          f"{len(known)} on file")
    check("and every one it offers really has one",
          all("@" in r["email"] for r in known))

    # **Found by searching, which is what a rep does.** The unfiltered list is
    # the 200 newest buyers -- a picker, not an index -- so asserting an
    # arbitrary older lead appears in it only passed while this database was
    # small enough that 200 was everyone. It stopped the day the row count
    # crossed the cap, naming the composer for what was really an ordering
    # accident. `q` is the control the composer actually offers, so it is the
    # one the gate drives.
    hunted = call(
        "GET", f"/api/email/recipients?q={urllib.parse.quote(send['to_address'])}"
    )["recipients"]
    target = next((r for r in hunted if r["lead_id"] == send["lead_id"]), None)
    check("and a rep typing an address finds the buyer behind it",
          target is not None, f"{send['to_address']} -> {len(hunted)} match(es)")
    wrote = call("POST", "/api/email/compose", {
        "to": target["email"], "subject": f"Checking in {run}",
        "body": "Are you still looking?",
    })
    check("a composed email is filed against the buyer it was addressed to",
          wrote["lead_id"] == target["lead_id"], str(wrote.get("lead_id")))
    # An outreach entry carries the row's own id, so this is the send itself
    # appearing in the buyer's history rather than something merely shaped
    # like it.
    check("and lands on their timeline like any other send",
          any(e["kind"] == "outreach" and e["id"] == wrote["id"]
              for e in call("GET", f"/api/leads/{target['lead_id']}/timeline")["entries"]),
          wrote["id"])
    check("with a reply route home, or the answer arrives nowhere",
          any(s2["id"] == wrote["id"]
              for s2 in call("GET", "/api/email/replyable")["sends"]))

    # A stranger is still writable-to. The row exists, it is listed here, and
    # it simply has no timeline to sit on -- which the composer states before
    # the rep presses send rather than after.
    stranger = call("POST", "/api/email/compose", {
        "to": f"nobody-{run}@example.invalid", "subject": "Hello",
        "body": "Who is this?",
    })
    check("writing to someone not on file is allowed, not refused",
          stranger["status"] == "sent", stranger["status"])
    check("and is honest that it belongs to nobody", stranger["lead_id"] is None)
    check("both appear in Sent", all(
        any(m["id"] == row["id"] for m in call("GET", "/api/email/messages?box=sent")["messages"])
        for row in (wrote, stranger)))

    # Refusals, because a composer is a form and a form gets submitted empty.
    no_to = status_of("POST", "/api/email/compose", {"to": "", "subject": "x", "body": "y"})
    check("an email with no recipient is refused", no_to[0] == 400, str(no_to[0]))
    hollow = status_of("POST", "/api/email/compose",
                       {"to": "a@b.invalid", "subject": " ", "body": " "})
    check("an empty email is refused rather than sent", hollow[0] == 400, str(hollow[0]))
    ghost = status_of("POST", "/api/email/compose",
                      {"to": "a@b.invalid", "subject": "x", "body": "y", "lead_id": "no-such"})
    check("and a send cannot be filed against a buyer who does not exist",
          ghost[0] == 404, str(ghost[0]))

    # Replying threads. The provider id of the message being answered goes out
    # as In-Reply-To, which is what puts our reply under the original in the
    # buyer's client instead of starting a second conversation.
    arrived = next(
        m for m in call("GET", "/api/email/messages?box=received")["messages"]
        if m["lead_id"]
    )
    answered = call("POST", "/api/email/compose", {
        "to": arrived["address"], "subject": f"Re: {arrived['subject']}",
        "body": "Thanks for getting back to us.", "lead_id": arrived["lead_id"],
        "in_reply_to_outreach_id": arrived["id"],
    })
    check("answering an arrival is recorded as a reply, not a fresh send",
          answered["kind"] == "reply", answered["kind"])
    check("and stays on the buyer whose message it answers",
          answered["lead_id"] == arrived["lead_id"])

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
    # A reply that does not thread reads as a new email from the dealership,
    # which is how a buyer ends up with four separate conversations about one
    # car. References as well as In-Reply-To: several clients thread on the
    # former only.
    threaded = resend.payload("b@e.com", "Re: x", "y", in_reply_to="<orig@ses>")
    check("a reply carries the header that threads it under the original",
          threaded["headers"]["In-Reply-To"] == "<orig@ses>"
          and threaded["headers"]["References"] == "<orig@ses>",
          str(threaded.get("headers")))
    check("and a fresh send carries no threading header at all",
          "headers" not in payload, str(sorted(payload)))

    # Who a message is *from*. Resend verifies the domain rather than the
    # mailbox, so one verified `linerai.us` makes every address on it legal to
    # send as -- which is what lets two founders each write under their own
    # name on one key, with no per-user credential anywhere.
    print("\n== an upgrade does not strand an existing install ==")
    # OWNER_PASSWORD arrived after the first deployments did, and it has a
    # development default -- so an install whose .env predates it stops booting
    # after an upgrade. The guard is right to fire; what it must not do is read
    # as "you misconfigured this" when nothing was misconfigured.
    import os
    import subprocess as _sp

    env = {
        **os.environ, "ENV": "production", "SESSION_SECRET": "x" * 40,
        "WEBHOOK_SECRET": "y" * 40, "MANAGER_PASSWORD": "real-one",
        "REP_PASSWORD": "real-two",
    }
    for key in ("OWNER_PASSWORD", "FOUNDER_PASSWORD", "CTO_PASSWORD"):
        env.pop(key, None)
    boot = _sp.run(
        [sys.executable, "-c", "import app.config"],
        cwd="backend", env=env, capture_output=True, text=True,
    )
    check("production still refuses to boot on a published password",
          boot.returncode != 0 and "FOUNDER_PASSWORD" in boot.stderr,
          boot.stderr.strip().splitlines()[-1][:70] if boot.stderr else "")
    # One key per person. A shared password is not a login: a leak cannot be
    # traced to anybody, and revoking it locks out whoever did not leak it.
    check("and it names each person's own key, not a shared one",
          "CTO_PASSWORD" in boot.stderr)
    check("and the message says it is the newer variable, not a mistake",
          "running before an upgrade" in boot.stderr,
          boot.stderr.strip().splitlines()[-1][:80] if boot.stderr else "")
    check("and names the command that repairs the database without a reseed",
          "add-owners" in boot.stderr)
    # The other half of the same trap: the accounts are created by a fresh
    # seed, so a database already taking bookings has none and `make reset-db`
    # would take the leads with it.
    from app.add_owners import add_owners
    check("adding them to a database that already has them changes nothing",
          add_owners() == (0, 0))
    # Our accounts share the users table with the dealership's, so every
    # unfiltered query(User) was a place they could surface inside somebody
    # else's showroom. One predicate covers the roster, the three assignment
    # paths and the public door.
    roster = call("GET", "/api/team")["members"]
    check("and a dealership's team page never lists them",
          not [u for u in roster if u["role"] == "owner"], f"{len(roster)} on the roster")

    # Our accounts are in `ops_users` now, not a role on the dealership's
    # table. That fixes a class of bug rather than three instances: an
    # unfiltered query(User) simply cannot reach one.
    from app.db import SessionLocal as _Session
    from app.models import OpsUser as _OpsUser, User as _User
    with _Session() as _db:
        owner_id = _db.query(_OpsUser).first().id
        strays = _db.query(_User).filter(_User.role.notin_(("manager", "rep"))).count()
    check("and `users` holds nobody but the dealership's own staff", strays == 0,
          f"{strays} stray row(s)")
    lead_id = call("GET", "/api/leads")["leads"][0]["id"]
    check("a buyer cannot be assigned to one",
          status_of("POST", f"/api/leads/{lead_id}/assign", {"user_id": owner_id})[0] == 404)
    appointment = call("GET", "/api/appointments")["appointments"][0]["id"]
    check("nor can an appointment",
          status_of("POST", f"/api/appointments/{appointment}/assign",
                    {"user_id": owner_id})[0] == 404)
    check("and a manager cannot administer or deactivate one",
          status_of("PATCH", f"/api/team/{owner_id}", {"daily_cap": 99})[0] == 404)

    print("\n== a person's own name on the envelope ==")
    from app.config import settings as app_settings
    from app.integrations.email.outbox import OutboxSender
    from app.outreach_send import identity_for

    class _Person:
        def __init__(self, name: str, email: str) -> None:
            self.name, self.email = name, email

    founder = _Person("Liner Founder", "founder@linerai.us")
    was_domain, was_from = app_settings.sending_domain, app_settings.sending_from
    try:
        app_settings.sending_domain = "linerai.us"
        app_settings.sending_from = "support@linerai.us"
        outbox = OutboxSender()

        mine = identity_for(outbox, founder)
        check("a founder sends under their own name once the domain is verified",
              mine.personal and mine.from_address == "Liner Founder <founder@linerai.us>",
              mine.from_address)
        check("and the display name is a real header, not an f-string",
              identity_for(outbox, _Person("Vale, Marcus", "m@linerai.us")).from_address
              == '"Vale, Marcus" <m@linerai.us>',
              identity_for(outbox, _Person("Vale, Marcus", "m@linerai.us")).from_address)
        # The whole message is rejected by a provider if the From is not on a
        # domain it has verified, so guessing is worse than falling back.
        stranger = identity_for(outbox, _Person("Somebody", "someone@gmail.com"))
        check("an address off the sending domain is refused, not spoofed",
              not stranger.personal and "gmail.com" not in stranger.from_address,
              stranger.from_address)
        check("and the fallback says which setting decides it",
              "SENDING_DOMAIN" in stranger.note, stranger.note[:80])
        check("a reply still comes back to them either way",
              stranger.reply_to == "someone@gmail.com", stranger.reply_to)
        # The bare address is what is matched, never the display name: a name
        # is text somebody typed and it can carry an `@`.
        forged = identity_for(outbox, _Person("linerai.us", "attacker@evil.test"))
        check("a display name cannot smuggle a domain past the check",
              not forged.personal, forged.from_address)

        # The request body Resend would receive, asserted without being sent.
        resend_from = resend.payload(
            "b@e.com", "s", "b", from_address=mine.from_address)["from"]
        check("and resend is handed that From verbatim",
              resend_from == "Liner Founder <founder@linerai.us>", resend_from)
        fell_back = resend.payload(
            "b@e.com", "s", "b", from_address="someone@gmail.com")["from"]
        check("while one it cannot prove falls back at the wire, not just in the UI",
              fell_back == "support@linerai.us", fell_back)
        check("a send with nobody named is still from the deployment",
              resend.payload("b@e.com", "s", "b")["from"] == "support@linerai.us")

        # ---- whose name is on the envelope when nobody is personally named --
        #
        # `SENDING_FROM` used to be a whole header and `.env.example`
        # illustrated it with `Riverside Auto <support@linerai.us>` -- the one
        # line in the file people copy verbatim. So every deployment started
        # from it mailed as a fixture car dealership: a prospect's buyers, and
        # our own support replies, which are not from a dealership at all.
        # The address is configured; the name is served.
        from app.outreach_send import OPS_SENDER_NAME, dealership_from

        app_settings.sending_from = "Riverside Auto <support@linerai.us>"
        check("a display name left in SENDING_FROM is dropped, not sent",
              outbox.default_address() == "support@linerai.us",
              outbox.default_address())

        from app.db import SessionLocal as _EnvelopeSession

        with _EnvelopeSession() as _db:
            named = dealership_from(_db, outbox)
        # **The two realms send from two mailboxes, and that is a routing
        # fact.** They shared `support@`, which `is_ours` sends to /ops -- so a
        # buyer who composed a *fresh* message to the address printed on their
        # booking confirmation reached Liner rather than the dealership, and
        # nothing anywhere said so. Pressing Reply worked, because that goes to
        # `reply+<token>@`, which is exactly why it stayed invisible.
        from app.email_intake import is_ours as _is_ours

        # Derived, so `SENDING_FROM` is cleared for this: the block above
        # deliberately sets it to prove a display name is dropped, and testing
        # the override here would say nothing about the default every
        # deployment actually gets.
        was_override, app_settings.sending_from = app_settings.sending_from, ""
        try:
            dealer_box = outbox.default_address("dealership")
            ops_box = outbox.default_address("ops")
        finally:
            app_settings.sending_from = was_override
        check("a dealership sends from sales@, not from our support desk",
              dealer_box == "sales@linerai.us", dealer_box)
        check("so a buyer writing to the address they can see reaches them",
              not _is_ours(dealer_box), dealer_box)
        check("while Liner's own support mail still comes from support@",
              _is_ours(ops_box) and ops_box.startswith("support@"), ops_box)
        check("and a display name on either of our mailboxes is still legal",
              outbox.from_header(named) == named
              and outbox.from_header("Liner <support@linerai.us>")
              == "Liner <support@linerai.us>")
        check("an explicit SENDING_FROM still wins for the dealership",
              named.endswith("<support@linerai.us>"), named)
        dealership_name = (call("GET", "/api/showroom/dealership") or {}).get("name", "")
        check("the dealership signs its own outreach, from the row",
              named == f"{dealership_name} <support@linerai.us>", named)
        check("and the sender puts that on the wire rather than dropping it",
              resend.payload("b@e.com", "s", "b", from_address=named)["from"] == named)

        ours = identity_for(outbox, None, fallback_name=OPS_SENDER_NAME, realm="ops")
        check("our own support mail is signed Liner, never a dealership",
              ours.from_address == "Liner <support@linerai.us>", ours.from_address)
        check("and its Reply-To is an address, not a rendered header",
              ours.reply_to == "support@linerai.us", ours.reply_to)
        # The fallback is where the wrong name did the damage: it is taken
        # whenever the person's address is not on the verified domain, which is
        # every deployment before the domain is set up.
        outsider = identity_for(
            outbox, _Person("Somebody", "someone@gmail.com"),
            fallback_name=OPS_SENDER_NAME, realm="ops",
        )
        check("including when a person's own address could not be honoured",
              outsider.from_address == "Liner <support@linerai.us>",
              outsider.from_address)
        # And when it *can* be honoured. The address and the Reply-To are the
        # person's, so an answer still reaches whoever wrote -- but the name is
        # the desk's. It was `user.name`, so a support reply arrived from
        # "Liner Founder", which is a job title on an envelope.
        signed = identity_for(outbox, founder, fallback_name=OPS_SENDER_NAME, realm="ops")
        check("support mail is signed Liner even when it is personally sent",
              signed.personal and signed.from_address == "Liner <founder@linerai.us>",
              signed.from_address)
        check("and the reply still reaches the person who wrote it",
              signed.reply_to == "founder@linerai.us", signed.reply_to)
    finally:
        app_settings.sending_domain, app_settings.sending_from = was_domain, was_from

    # A display name on our *own* address is always legal -- it is our verified
    # mailbox wearing somebody's name, which is what every product's
    # transactional mail looks like. Gated on `can_send_as` it was dropped on
    # any deployment with no SENDING_DOMAIN, which is exactly when a person is
    # looking at the result.
    app_settings.sending_from = "support@linerai.us"
    try:
        bare_deploy = OutboxSender()
        check("a name on our own address survives with no domain configured",
              bare_deploy.from_header("Craig and Landreth Cars <support@linerai.us>")
              == "Craig and Landreth Cars <support@linerai.us>",
              bare_deploy.from_header("Craig and Landreth Cars <support@linerai.us>"))
        check("but somebody else's address still falls back there",
              bare_deploy.from_header("Someone <s@gmail.com>") == "support@linerai.us",
              bare_deploy.from_header("Someone <s@gmail.com>"))
    finally:
        app_settings.sending_from = was_from

    # The line people copy must not carry a fixture's name, which is where
    # this came from in the first place.
    env_example = pathlib.Path(".env.example").read_text()
    check("and .env.example no longer ships a dealership name to copy",
          not re.search(r"^\s*#?\s*SENDING_FROM=.*<", env_example, re.MULTILINE),
          [l for l in env_example.splitlines() if "SENDING_FROM=" in l])
    check("with no sending domain configured, nobody can send as anybody",
          not identity_for(OutboxSender(), founder).personal)

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

    print("\n== a call runs the same executors, and says what it cannot guard ==")
    # Voice is off in this environment, and the refusal is the assertion: a
    # typed 503 naming the variable, not a 500 and not a call that appears to
    # start. Inventing a key to turn it green is the opposite of the point.
    minted = status_of("POST", "/api/voice/sessions")
    check("with no provider a call is refused, in words that name the setting",
          minted[0] == 503 and "VOICE_PROVIDER" in minted[1],
          f"{minted[0]}: {minted[1][:90]}")

    # The dealership's greeting reaches the browser so the first turn can be
    # sent verbatim. Left to improvise an opening on an empty conversation, a
    # smaller model improvises the *customer's* -- which is what happened.
    greeting = call("GET", "/api/assistant-settings")["live"].get("greeting", "")
    check("the dealership has an opening line for the pre-roll to say",
          bool(greeting.strip()), greeting[:60])

    # The relay is the whole reason a call is as safe as a chat: the model asks,
    # our executor decides. So it is exercised directly, with no provider.
    # ?channel=voice, not a body field -- posting it in the body is silently
    # ignored, which had this whole section exercising a chat thread while
    # claiming to test a call.
    voice_convo = call("POST", "/api/chat/sessions?channel=voice", {})
    vid = voice_convo.get("conversation_id") or voice_convo.get("id")
    check("and it really is a call, not a chat wearing the label",
          call("GET", f"/api/conversations/{vid}")["channel"] == "voice",
          call("GET", f"/api/conversations/{vid}")["channel"])
    hidden = call("POST", "/api/voice/tools", {
        "conversation_id": vid, "name": "search_inventory",
        "input": {"keywords": "BMW 330i"},
    })["result"]
    check("a tool call over the wire runs the real executor",
          "vehicles" in hidden, str(sorted(hidden))[:70])
    check("and a do-not-discuss vehicle is withheld on a call too",
          not any("330i" in (v.get("model") or "") for v in hidden["vehicles"]),
          str([v.get("model") for v in hidden["vehicles"]][:4]))

    bad = call("POST", "/api/voice/tools", {
        "conversation_id": vid, "name": "search_inventory", "input": {"nonsense": 1},
    })
    check("an invented argument is an error the model can see, not a crash",
          "error" in bad, str(bad)[:80])

    # The transcript is where the guard has to run, because on a call the words
    # are already spoken by the time any server has them. It cannot unsay a
    # price -- it raises a person, and this asserts that rather than implying
    # parity with chat.
    call("POST", "/api/voice/transcript", {
        "conversation_id": vid, "role": "buyer", "content": "how much is the Corolla?",
    })
    flagged = call("POST", "/api/voice/transcript", {
        "conversation_id": vid, "role": "assistant",
        "content": "That one is $18,400 out the door.",
    })
    check("an unsourced price spoken on a call is caught on the transcript",
          bool(flagged.get("guard_violations")), str(flagged.get("guard_violations")))
    queued = call("GET", "/api/conversations?filter=needs_person")
    rows = queued.get("conversations", queued if isinstance(queued, list) else [])
    check("and it puts a rep on the call rather than passing silently",
          any(r.get("id") == vid for r in rows), vid)

    # The other half, and the half that would be invisible if it broke: a
    # number the buyer said themselves must not trip it. A guard that fires on
    # every budget question puts a rep on every call and reads as a dead bot.
    call("POST", "/api/voice/transcript", {
        "conversation_id": vid, "role": "buyer",
        "content": "my budget is about $22,500",
    })
    honest = call("POST", "/api/voice/transcript", {
        "conversation_id": vid, "role": "assistant",
        "content": "Right -- so under $22,500. Let me see what we have.",
    })
    check("while a number the buyer said themselves is left alone",
          not honest.get("guard_violations"), str(honest.get("guard_violations")))

    # A real call recorded a buyer message reading a Chinese filler particle,
    # from an English speaker who had made an "mm" sound -- and a later one
    # recorded "\u6bd4\u514b\u62c9\u65af" from someone saying "E-Class". The session names
    # the language, but the vendor treats that as a preference rather than a
    # constraint, so the channel enforces it here: an English-only call did not
    # produce a line with no English in it, and a rep reads this before phoning
    # the buyer back.
    grunt = call("POST", "/api/voice/transcript", {
        "conversation_id": vid, "role": "buyer", "content": "\u55ef",
    })
    check("a non-verbal sound is not recorded as something the buyer said",
          grunt.get("recorded") is False, str(grunt)[:60])
    mangled = call("POST", "/api/voice/transcript", {
        "conversation_id": vid, "role": "buyer", "content": "\u6bd4\u514b\u62c9\u65af",
    })
    check("nor is a whole line decoded into the wrong script",
          mangled.get("recorded") is False, str(mangled)[:60])
    # Dropping a message a buyer really sent is far worse than keeping one they
    # did not, so anything with an English letter in it stays however short --
    # and so does a bare number, which is a year, a price or a phone number.
    for content, label in (
        ("ok", "while a real word is kept however short"),
        ("E-Class", "and a trim the transcriber got right is kept"),
        ("2019", "and so is a bare number -- a year, a price, a phone number"),
    ):
        kept = call("POST", "/api/voice/transcript", {
            "conversation_id": vid, "role": "buyer", "content": content,
        })
        check(label, kept.get("recorded") is not False, str(kept)[:60])

    # What a call cost, from the provider's own token counts rather than from
    # wall-clock. A realtime call bills the whole conversation so far as input
    # on every turn, so the per-minute average hides the only thing that
    # matters -- whether the cache is catching that history or it is being
    # paid for again each time.
    usage = {
        "input_tokens": 5000,
        "input_token_details": {
            "audio_tokens": 1600, "text_tokens": 3400, "cached_tokens": 4000,
            "cached_tokens_details": {"audio_tokens": 1000, "text_tokens": 3000},
        },
        "output_tokens": 200,
        "output_token_details": {"audio_tokens": 180, "text_tokens": 20},
    }
    first = call("POST", "/api/voice/usage", {
        "conversation_id": vid, "response_id": f"resp-{run}", "usage": usage})
    check("a call's token usage is recorded, not estimated",
          first.get("recorded") is True, str(first))
    # A relay that retries must not double a call's apparent cost. A cost
    # report nobody trusts is one nobody reads.
    again = call("POST", "/api/voice/usage", {
        "conversation_id": vid, "response_id": f"resp-{run}", "usage": usage})
    check("and a retried relay does not double it",
          again.get("recorded") is False, str(again))

    priced = call("GET", f"/api/voice/cost/{vid}")
    check("the cost is broken down per response, not just totalled",
          len(priced["turns"]) == 1 and priced["responses"] == 1, str(priced["responses"]))
    # Cached input is discounted by roughly eighty times, so charging the
    # cached tokens at the full rate as well would wipe the discount out --
    # and the reported audio and text counts *include* the cached part.
    check("cached input is separated from fresh, or the discount is lost",
          priced["turns"][0]["cached_tokens"] == 4000
          and priced["turns"][0]["fresh_input_tokens"] == 1000,
          str(priced["turns"][0]))
    check("and the cache hit ratio is reported, because it is the whole bill",
          priced["cache_hit_ratio"] == 0.8, str(priced["cache_hit_ratio"]))
    check("with a dollar figure that is labelled an estimate",
          priced["estimated_usd"] > 0 and "authority" in priced["note"],
          f"${priced['estimated_usd']}")
    # Whether the money means anything at all. An unknown model is reported
    # unpriced rather than charged at another model's rates.
    check("and it says whether the model's rates were actually known",
          priced["priced"] is True, f"{priced['model']}: {priced['note'][:60]}")

    # Recording. Bytes on disk, a row pointing at them, and a session in front
    # of the playback -- it is somebody's voice.
    head = b"\x1aE\xdf\xa3" + b"first slice " * 20
    tail = b"second slice " * 20
    part = upload_audio(vid, "audio/webm", head, complete=False, seq=0)
    check("a slice of call audio is stored as it is recorded",
          part.get("stored") is True, str(part))
    grew = upload_audio(vid, "audio/webm", tail, duration_ms=61000, complete=True, seq=1)
    check("and the next slice is appended to it, not written over it",
          grew.get("bytes") == len(head) + len(tail),
          f"{grew.get('bytes')} of {len(head) + len(tail)}")

    check("an unsupported type is refused before anything is written",
          status_of("POST", f"/api/voice/recording/{vid}/chunk")[0] in {415, 422},
          "no file / wrong type")

    played = fetch_audio(vid)
    check("a rep can play the call back, slices in the order they arrived",
          played[0] == 200 and played[1] == head + tail,
          f"{played[0]}, {len(played[1])} bytes")
    check("but not without a session -- it is somebody's voice",
          fetch_audio(vid, anonymous=True)[0] in {401, 403},
          str(fetch_audio(vid, anonymous=True)[0]))

    ended = call("POST", f"/api/voice/sessions/{vid}/end", {})
    check("a call ends without needing a provider", bool(ended.get("ok")))
    # Stamped on hang-up as well as on close_conversation. Without it a call the
    # buyer simply dropped had no end time, so its length was unknowable.
    check("and the call has a length afterwards", "seconds" in ended, str(ended))

    # The audio and the length arrive on the buyer's timeline as one entry --
    # "a call on Tuesday" rather than a header over forty transcript lines.
    #
    # Against a call that has a buyer, which the one above does not: a thread
    # only gets a lead when something books. Hunted for rather than assumed,
    # because a check wrapped in `if` is a check that quietly stops running.
    with_lead = next(
        (r for r in call("GET", "/api/conversations")["conversations"]
         if r.get("channel") == "voice" and r.get("lead")),
        None,
    )
    check("the seed has a call that belongs to a buyer", with_lead is not None)
    other = with_lead["id"]
    upload_audio(other, "audio/mp4", b"\x00\x00\x00 ftypM4A demo", duration_ms=44000)
    line = next(
        (e for e in call("GET", f"/api/leads/{with_lead['lead']['id']}/timeline")["entries"]
         if e["kind"] == "call" and e["id"] == other),
        None,
    )
    check("a call is one timeline entry, not a header over its transcript",
          line is not None, str(line)[:70])
    check("carrying its own audio and its own length",
          line["has_recording"] is True and line["seconds"] >= 0, str(line)[:90])
    # Safari records mp4 and Chrome records webm. Serving one as the other
    # plays silence, so what the browser produced is what comes back.
    played_mp4 = fetch_audio(other)
    check("and served back as the type the browser actually recorded",
          played_mp4[0] == 200 and played_mp4[1].startswith(b"\x00\x00\x00 ftyp"),
          str(played_mp4[0]))
    # Two calls, two files. The filename is built from the row id, which is
    # generated at flush -- read a moment too early it is None, and every
    # recording on the system lands in one file called None.webm. That shipped
    # once and was invisible while each call uploaded exactly once.
    check("and one call's audio is not another call's file",
          fetch_audio(vid)[1] != played_mp4[1],
          f"{len(fetch_audio(vid)[1])} vs {len(played_mp4[1])} bytes")
    kept = [r for r in call("GET", "/api/voice/recordings")["recordings"]
            if not r["orphaned"]]
    check("and each call has a file of its own on disk",
          len({r["filename"] for r in kept}) == len(kept), f"{len(kept)} recordings")
    # Rows written before the filename was built after the flush all name one
    # file. They are refused rather than served, because serving them hands one
    # buyer's call audio to another buyer's page.
    stale = next(
        (r for r in call("GET", "/api/voice/recordings")["recordings"] if r["orphaned"]),
        None,
    )
    if stale:
        check("and a recording from the shared-file era is refused, not served",
              fetch_audio(stale["conversation_id"])[0] == 410,
              str(fetch_audio(stale["conversation_id"])[0]))

    # ----------------------------------------------------------------------
    # The buyer's own track, and the timeline the two halves are joined on.
    #
    # Transcribing the mix is not an option: one track carrying two speakers
    # gives an undifferentiated stream of words with no way to tell who said
    # which. A track with exactly one speaker on it cannot mis-attribute a
    # line, which is why the microphone is recorded a second time.
    # ----------------------------------------------------------------------
    mic = b"\x1aE\xdf\xa3" + b"buyer only " * 20
    apart = upload_audio(vid, "audio/webm", mic, complete=False, track="buyer")
    check("the buyer's microphone is recorded to a track of its own",
          apart.get("stored") is True and apart.get("track") == "buyer", str(apart))
    check("and it does not land in the file a rep plays back",
          fetch_audio(vid)[1] == head + tail,
          f"{len(fetch_audio(vid)[1])} bytes, expected {len(head + tail)}")
    check("an unknown track is refused rather than opening a third file",
          status_of("POST", f"/api/voice/recording/{vid}/chunk?track=nonsense")[0]
          in {400, 415, 422},
          "track=nonsense")

    # Marks, stamped in the browser's clock. Liner's carry its own words --
    # the model emits the text alongside the audio, so they are exact -- and
    # the buyer's carry none, because those words are recovered from the audio
    # afterwards.
    marked = call("POST", "/api/voice/segments", {
        "conversation_id": vid,
        "segments": [
            {"speaker": "assistant", "started_ms": 500, "ended_ms": 4000,
             "text": "Riverside Auto, this is Liner.", "source": "model"},
            {"speaker": "buyer", "started_ms": 5000, "ended_ms": 9000},
            {"speaker": "assistant", "started_ms": 10000, "ended_ms": 13000,
             "text": "We have two of those on the lot.", "source": "model"},
        ],
    })
    check("speech marks are stored as the call produces them",
          marked.get("stored") == 3, str(marked))
    # An assistant mark with no text is nothing at all: unlike a buyer span,
    # there is no later pass that could fill it in.
    empty = call("POST", "/api/voice/segments", {
        "conversation_id": vid,
        "segments": [{"speaker": "assistant", "started_ms": 1, "ended_ms": 2}],
    })
    check("but a wordless mark from Liner is not, since nothing can fill it",
          empty.get("stored") == 0, str(empty))

    reading = call("GET", f"/api/voice/transcript/{vid}")
    check("the transcript is one ordered list built from those marks",
          [l["speaker"] for l in reading["lines"]] == ["assistant", "assistant"],
          str([l["speaker"] for l in reading["lines"]]))
    # Each line says where it came from. `model` is Liner quoting itself and is
    # exact; `live` is the streaming transcriber's guess -- the one that turns
    # "E-Class" into 比克拉斯 -- and `recorded` is the version taken from the
    # buyer's own track afterwards. A rep about to ring someone back should be
    # able to tell which of those they are reading.
    check("and every line says which of those it is",
          {l["source"] for l in reading["lines"]} == {"model"},
          str({l["source"] for l in reading["lines"]}))
    check("with the buyer's track recorded but not yet transcribed",
          reading["buyer_track_bytes"] == len(mic) and reading["transcribed_at"] is None,
          f"{reading['buyer_track_bytes']} bytes, {reading['transcribed_at']}")

    # Transcribing afterwards is a second model on a second bill, reached at a
    # different endpoint from the call. Without a key it says which variable is
    # missing rather than failing vaguely -- and the live transcript stands.
    after = call("POST", f"/api/voice/transcribe/{vid}")
    check("transcribing after the call names the key it wants, or does the work",
          after.get("error") == "not_configured" or after.get("transcribed") is True,
          str(after)[:90])
    if after.get("error") == "not_configured":
        check("and names the variable rather than failing vaguely",
              after["missing"] == ["OPENAI_API_KEY"], str(after["missing"]))
        check("while the transcript it could not improve is still there",
              len(call("GET", f"/api/voice/transcript/{vid}")["lines"]) == 2,
              "live lines kept")

    # A call with no buyer track cannot be transcribed, and says so rather than
    # reporting a success that produced nothing.
    check("a call whose microphone was never recorded says so",
          call("POST", f"/api/voice/transcribe/{other}").get("reason", "").startswith("no buyer"),
          str(call("POST", f"/api/voice/transcribe/{other}"))[:80])

    # Inventory is the local database and says so. It used to report itself
    # unconfigured for want of SCRAPER_BASE_URL, which put it in the amber
    # banner beside things that really are missing -- and a banner that cries
    # wolf is a banner nobody reads.
    integrations = call("GET", "/api/integrations")
    source = next(i for i in integrations["integrations"] if i["key"] == "scraper")
    check("inventory reports the database as a real source, not a gap",
          source["configured"] and source["impl"] in {"database", "http"},
          f"{source['impl']}: {source['detail'][:60]}")
    check("and is not counted among the unconfigured",
          "scraper" not in integrations["unconfigured"],
          str(integrations["unconfigured"]))

    print("\n== every screen the SPA owns is actually served ==")
    # `/ops` shipped missing from SPA_PREFIXES, so on a real host the whole ops
    # dashboard answered {"detail":"Not found"} -- FastAPI's JSON 404 from the
    # catch-all, which means the request never reached React and no session,
    # role or redirect logic ever ran. Every gate was green, because every
    # browser check drives Vite on :5173 and Vite's history fallback serves
    # index.html for any path at all. Only the built bundle enforces the list,
    # and nothing was reading it.
    sys.path.insert(0, "backend")
    from app.static import DIST, RESERVED, SPA_PREFIXES

    routes = re.findall(r'<Route\s+path="(/[^"*]*)"', TSX.read_text())
    top = sorted({"/" + r.strip("/").split("/")[0] for r in routes if r.strip("/")})
    check("main.tsx has the top-level routes this expects", len(top) >= 5, str(top))
    missing = [r for r in top if r not in SPA_PREFIXES]
    check("every top-level SPA route is one the API will serve", not missing,
          f"missing from SPA_PREFIXES: {missing}")
    stale = [p for p in SPA_PREFIXES if p not in top]
    check("and nothing is listed that the SPA no longer routes", not stale, str(stale))

    # The list is only half of it: assert the built bundle really answers. This
    # section is the one place the *production* path is exercised, so it runs
    # only when there is a build to exercise.
    if (DIST / "index.html").is_file():
        for path in SPA_PREFIXES:
            status, body = status_of("GET", path)
            check(f"{path} serves the app rather than a JSON 404",
                  status == 200 and "<div id=" in body, f"{status} {body[:40]}")
        check("the landing page is still what / serves",
              "<!doctype html" in status_of("GET", "/")[1].lower())
        for reserved in RESERVED:
            check(f"/{reserved} is still reserved, not swallowed by the catch-all",
                  status_of("GET", f"/{reserved}/nothing-here")[0] == 404)
        check("and a mistyped path still says so instead of returning a page",
              status_of("GET", "/notaroute")[0] == 404)
    else:
        print("  [skip] no frontend/dist -- run `make build` to check the served paths")

    # A chip's message_text is sent as the buyer's own words, and
    # `save_captured_fields` records what it contains as `typed` -- the
    # provenance that means *the buyer said this*. One chip carried a fixture
    # buyer's name and address, so a real person tapping it told Liner they
    # were Jordan Reyes, and a rep then rang Jordan Reyes.
    from app.db import SessionLocal as _RailSession
    from app.models import Rail as _Rail

    _rdb = _RailSession()
    try:
        texts = [(r.label, r.message_text) for r in _rdb.query(_Rail).all()]
    finally:
        _rdb.close()
    invented = [
        (label, text) for label, text in texts
        if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        or re.search(r"\bI'm [A-Z][a-z]+ [A-Z][a-z]+", text)
    ]
    check("no chip puts a name or an address in the buyer's mouth", not invented,
          str(invented))

    print("\n== chips that answer themselves ==")
    # A rail is a button the dealership put on screen and its meaning is
    # fixed: "What's under $20k?" can only mean one search. Sending that to a
    # model asks it to re-derive an intent somebody already decided, and pays
    # a round trip in front of a buyer for the privilege.
    from app.agent import rail_actions as _actions

    chip_convo = call("POST", "/api/chat/sessions")
    chip_id = chip_convo["conversation_id"]
    opening = chip_convo["rails"]
    check("the opening chips are the dealership's own", len(opening) >= 3,
          str([r["label"] for r in opening]))

    reply, state, events = say(chip_id, rail_id=pick(opening, "under $20k"))
    said = (reply or {}).get("content", "")
    shown = [
        v for _e, d in events if _e == "vehicles" for v in (d.get("vehicles") or [])
    ]
    check("pressing one answers from the tool, not from a model",
          "under $20,000" in said, said.splitlines()[0] if said else "(nothing)")
    check("and it puts cars on the screen", len(shown) > 0, f"{len(shown)} card(s)")
    over = [v for v in shown if (v.get("price") or 0) >= 20000]
    check("every car it names really is under that price", not over,
          str([v.get("price") for v in over]))

    # A price from an earlier turn is deliberately not sourced -- prices are
    # re-read every turn because they can change -- so a relative chip must
    # not speak its bound. Written with the number in, the guard rejected the
    # whole reply and the buyer read the escalation line instead of three
    # cars. Measured; this is the check that caught it.
    cheaper_chip = pick(state["rails"], "cheaper")
    check("a chip relative to what is on screen is offered once something is",
          bool(cheaper_chip), str([r["label"] for r in state["rails"]]))
    if cheaper_chip:
        reply2, _state2, _events2 = say(chip_id, rail_id=cheaper_chip)
        answer = (reply2 or {}).get("content", "")
        # Two honest outcomes: cheaper cars, or "those are the cheapest".
        # The failure being watched for is the third one -- the guard's
        # escalation line, which is what a bound quoted from a previous turn
        # produced, and which reads to a buyer like a dead bot.
        check("and it answers rather than tripping the price guard",
              "person on our team" not in answer.lower()
              and "confirm that before I answer" not in answer,
              answer[:60])
        check("without quoting a bound it was not just handed",
              not re.search(r"under \$[\d,]+", answer) or "cheaper:" in answer,
              answer[:60])

    # "Anything cheaper?" before anything has been shown is a question about
    # nothing: there is no price to be cheaper than.
    check("and is not offered before anything has been shown",
          pick(opening, "cheaper") is None and pick(opening, "lower mileage") is None,
          str([r["label"] for r in opening]))

    # Free text still goes to the model. This is not a return to the scripted
    # assistant -- what is short-circuited is only the pre-written question.
    actions = {name for name in _actions.ACTIONS}
    check("only chips with a fixed meaning carry one",
          actions == {"under_price", "with_seats", "matching", "cheaper", "fewer_miles"},
          str(sorted(actions)))
    check("and a malformed action falls back to the model rather than raising",
          _actions.action_of(SimpleNamespace(action_json="{not json")) == ("", {}))

    print("\n== the dealership is served, never written into a page ==")
    import yaml as _yaml

    # Five surfaces printed the literal string "Riverside Auto" -- the chat
    # header, the call header, the login subtitle and two lines on the buyer
    # page. On a rebranded instance every one of them told a prospect's buyer
    # they were talking to somebody else's showroom, and the very first screen
    # of a demo is the one that has to be right.
    for page in ("Chat.tsx", "Call.tsx", "Login.tsx", "LeadPage.tsx"):
        source = (pathlib.Path("frontend/src/routes") / page).read_text()
        # Comments may name it -- that is where the story of the bug lives.
        rendered = "\n".join(
            line for line in source.splitlines()
            if not line.strip().startswith(("*", "//", "/*"))
        )
        check(f"{page} reads the dealership's name rather than printing one",
              "Riverside Auto" not in rendered)

    shop = call("GET", "/api/showroom/dealership")
    check("and the server answers with it, without a session",
          bool(shop["name"]) and "brand" in shop, shop["name"])

    front = call("GET", "/api/showroom?limit=3")
    check("the showroom page has the lot, the greeting and the channels",
          front["total"] > 0 and front["greeting"] and "voice" in front["channels"],
          f"{front['total']} cars")

    # Their own front page prints "Chevrolet (74)" beside each make and offers
    # four price bands. Both are honest only if they come from rows: a browse
    # filter promising 74 cars and showing 9 is worse than no filter, and it
    # is the easiest thing on a demo page to get wrong.
    facets = front["facets"]
    bands = sum(b["count"] for b in facets["price_bands"])
    priced = call("GET", "/api/showroom?limit=1&min_price=0")["total"]
    check("the price bands partition the lot rather than overlapping it",
          bands == priced, f"{bands} across bands vs {priced} priced cars")

    top = facets["makes"][0]
    narrowed = call("GET", f"/api/showroom?limit=100&make={quote(top['name'])}")
    check("a make count is the number of cars that make actually returns",
          narrowed["total"] == top["count"], f"{top['name']}: {top['count']} vs {narrowed['total']}")

    # Tokenised on whitespace, "BMW X5?" gives `x5?` and matches nothing --
    # the exact bug that made the most natural phrasing the one that failed in
    # the chat, while "BMW X5" worked and kept it invisible. Here the box is a
    # keyword box rather than the chat, so every word has to hit; what it must
    # not do is let a question mark decide the answer.
    ask = lambda text: call("GET", f"/api/showroom?limit=5&q={quote(text)}")["total"]
    plain = ask("BMW X5")
    check("the search box narrows to the car that was named", plain == 1, str(plain))
    check("and a question mark does not change the answer", ask("BMW X5?") == plain)
    check("nor does the case somebody happens to type",
          ask("bmw   x5") == plain)

    # Their copy, served rather than written into the component. Hardcoding a
    # prospect's own sentences is the "Riverside Auto" bug one level up.
    craig = _yaml.safe_load(
        pathlib.Path("backend/config/dealerships/craigandlandreth.yaml").read_text())
    page = pathlib.Path("frontend/src/routes/Showroom.tsx").read_text()
    check("the prospect's profile carries their front page, not the component",
          bool((craig.get("site") or {}).get("heading"))
          and (craig["site"]["heading"] not in page),
          craig.get("site", {}).get("heading", ""))
    check("and their opening hours are filled in, so the seed will run",
          any((craig.get("hours") or {}).values()))
    # A public page, so it must not carry what the dealer's own view carries:
    # `rule_note` is an internal line reading "Consignment, owner has not
    # signed the agreement yet", and `mention_count` is how many buyers have
    # been quoted the car.
    leaked = [k for k in ("rules", "mention_count", "internal_note", "source")
              if front["vehicles"] and k in front["vehicles"][0]]
    check("and none of the dealer's own columns with it", not leaked, str(leaked))

    # One predicate, not two. A car Liner refuses to discuss sitting on the
    # page next to the chat window refusing to discuss it is the whole failure.
    check("the page and the tool narrow the lot with the same rule",
          "offerable" in pathlib.Path("backend/app/api/showroom.py").read_text()
          and "offerable" in pathlib.Path("backend/app/agent/tools.py").read_text())

    # The made-up showroom is Riverside's. Seeded into a prospect's instance
    # it is not a head start, it is wrong data: their buyer searches the lot
    # and is offered a Toyota Sienna from Cedar Falls, Iowa, then asks the doc
    # fee and is told $189 because a fixture said so.
    from app.seed import _has_fixture, _knowledge_for, _possessive

    profiles = sorted(pathlib.Path("backend/config/dealerships").glob("*.yaml"))
    check("there is a profile per prospect", len(profiles) >= 2,
          str([p.stem for p in profiles]))
    for path in profiles:
        raw = _yaml.safe_load(path.read_text()) or {}
        if path.stem == "riverside":
            check("riverside owns the showroom fixture", _has_fixture(raw))
            continue
        check(f"{path.stem} does not inherit it", not _has_fixture(raw))
        # A prospect may write policies of their own -- Craig and Landreth's
        # doc fee is theirs, confirmed by their manager. What must never happen
        # is inheriting Riverside's, which is where "$189" came from. The
        # *topic* is legitimately shared, because it is the words a buyer types
        # ("Doc fee" is matched by "what's your doc fee?"); the answer is the
        # part that would be a policy nobody at that dealership agreed to.
        from app.seed import KNOWLEDGE as _riverside_knowledge

        borrowed = {a for _t, a in _knowledge_for(raw)} & {a for _t, a in _riverside_knowledge}
        check(f"and {path.stem} states its own policies or none, never Riverside's",
              not borrowed, str(sorted(borrowed))[:120])
    # A reseed has to survive a database that has been used. Four call tables
    # and inbound_emails were added after `_clear` was written and none was
    # added to it, so on any box that had taken a call or received a reply --
    # every box a demo has been rehearsed on -- reseeding died on `DELETE FROM
    # outreach` with a bare FOREIGN KEY error naming no table. Invisible
    # because `make reset-db` deletes the file first and never reaches it.
    import app.models  # noqa: F401 -- registers every table on the metadata
    from app.db import Base as _Base
    from app.seed import _clear as _clear_fn

    source = pathlib.Path("backend/app/seed.py").read_text()
    # Read the models `_clear` names out of the function itself, so this
    # notices a table dropped from the list as well as one never added.
    body = source.split("def _clear")[1].split("db.commit()")[0]
    emptied = {
        table.name for cls, table in
        ((c, c.__table__) for c in _Base.__subclasses__())
        if cls.__name__ in set(re.findall(r"\b[A-Z][A-Za-z]+\b", body))
    }
    dangling = sorted(
        name for name, table in _Base.metadata.tables.items()
        if name not in emptied
        and not name.startswith("ops_")
        and name != "inbound_emails"
        and {fk.column.table.name for fk in table.foreign_keys} & emptied
    )
    check("nothing points at a table the reseed empties without being emptied too",
          not dangling, str(dangling))
    # inbound_emails is the exception, and it is detached rather than deleted:
    # a delivery nobody could place is listed in OUR mailbox at /ops, so
    # dropping it on a dealership reseed is mail thrown away on somebody's
    # behalf -- the same failure `_clear` avoids for the ops_ tables.
    check("and a delivery receipt is detached on a reseed, never destroyed",
          "_unplace_inbound" in source and "InboundEmail" not in
          source.split("def _clear")[1].split("db.commit()")[0])

    # Their livery. `surface` picks a stylesheet class rather than carrying a
    # value into one, so an unknown word must never reach the DOM -- and it is
    # read only by /showroom: a rep's dashboard is a working tool and should
    # not change colour because a prospect's marketing site is dark.
    from app.profile import brand as _brand_fn

    check("a brand surface is one of two words, never whatever was typed",
          shop["brand"]["surface"] in ("light", "dark"), shop["brand"]["surface"])
    craig_brand = craig.get("brand") or {}
    check("and the prospect's accent is a hex the stylesheet will accept",
          bool(re.match(r"^#[0-9a-fA-F]{3,8}$", craig_brand.get("accent") or "")),
          craig_brand.get("accent", ""))
    css = pathlib.Path("frontend/src/styles/liner-theme.css").read_text()
    check("the dark palette lives in the one token layer, not in the page",
          ".dark {" in css and "dark" in page and "oklch" not in page)

    # Staff arrive through `_seed_users`, which only runs on a fresh seed, so
    # giving a real person an account used to mean `make reset-db` -- which
    # deletes every lead on the box.
    from app.add_user import ROLES as _ROLES, initials as _initials

    check("a person can be added to the team without a reseed",
          pathlib.Path("backend/app/add_user.py").is_file()
          and "add-user" in pathlib.Path("Makefile").read_text())
    check("and only ever as dealership staff, never as one of ours",
          set(_ROLES) == {"manager", "rep"}, str(sorted(_ROLES)))
    check("their avatar comes from their name", _initials("Austin Reed") == "AR")

    # Their listing URL and their store id are facts about the dealership, so
    # they live in the profile. As environment variables, switching
    # DEALERSHIP= to a second prospect and leaving SCRAPER_BASE_URL alone
    # crawled the first dealer's site into the second one's instance --
    # silently, because a successful crawl of the wrong site looks exactly
    # like a successful crawl of the right one.
    from app.profile import inventory as _inventory

    check("the crawl source comes from the profile, not from .env",
          "source_url" in (craig.get("inventory") or {}),
          str(sorted((craig.get("inventory") or {}))))
    check("and the env vars remain the fallback, so a fixture site still works",
          _inventory()["origin"] in ("env", "none"), _inventory()["origin"])
    # Which store to keep cannot be baked in at import: the dealership is
    # chosen per deployment and read per request, so an adapter narrowed when
    # the process started is narrowed to whoever it started as.
    from app.ingest.sites.dealercarsearch import DealerCarSearch as _DCS

    check("and which lot to keep is decided per crawl, not at import",
          _DCS().dealer_id == "" and _DCS().for_dealer("1123").dealer_id == "1123")
    check("narrowing returns a copy, so one crawl cannot bind the next",
          _DCS().for_dealer("1123") is not _DCS())

    # Two halves that refresh differently: rows come from the seed, the
    # profile file is read per request but chosen once at startup. Editing
    # .env without reseeding -- or reseeding without restarting -- leaves a
    # dashboard showing one dealership over a storefront wearing the other's
    # colours, which reads as a broken page and is nothing of the kind.
    check("a mismatch between the seeded rows and the loaded profile is shouted about",
          "_report_dealership" in pathlib.Path("backend/app/main.py").read_text())

    # A prospect's instance should not ship with Dana Mercer on the roster.
    check("a profile can carry its own staff, so a reseed rebuilds them",
          [p["email"] for p in (craig.get("staff") or [])] != [],
          str([p.get("email") for p in (craig.get("staff") or [])]))
    check("and only ever as manager or rep",
          all((p.get("role") or "rep") in ("manager", "rep") for p in (craig.get("staff") or [])))

    # The seed prints logins, and it printed a hardcoded pair -- so a profile
    # that seeds its own staff ended by naming two accounts it had not
    # created. Somebody reads that line, types it, and gets a 401 that
    # correctly refuses to say whether the address exists. The report has to
    # come from the rows.
    seed_src = pathlib.Path("backend/app/seed.py").read_text()
    tail = seed_src.split("def seed(")[1]
    check("the seed reports the accounts it actually created, not a fixed pair",
          "dana.mercer" not in tail and "logins" in tail)

    check("the greeting is built from whichever name that is",
          _possessive("Riverside Auto") == "Riverside Auto's"
          and _possessive("Craig and Landreth Cars") == "Craig and Landreth Cars'",
          _possessive("Craig and Landreth Cars"))

    print("\n== every address we publish is one the intake accepts ==")
    # The Worker filters recipients before it posts anything, because a
    # catch-all sweeps up spam. `founder@` was not on that list while
    # landing.html published it as the way to reach a person directly -- so
    # the one address we tell people to write to was thrown away in
    # Cloudflare, with a console.log and nothing else. No receipt, no row, no
    # error: from this side it is indistinguishable from nobody writing.
    #
    # Nothing here can run a Worker, so what is checked is the agreement
    # between the two lists. That is the half that was actually wrong.
    worker = (pathlib.Path("backend/app/integrations/email/worker/src/index.ts")).read_text()
    prefixes = re.findall(r'"([a-z0-9_+@.-]+)"', worker.split("DEFAULT_PREFIXES")[1].split("]")[0])
    check("the worker declares the recipients it accepts", len(prefixes) >= 3, str(prefixes))

    from app.config import settings as _cfg
    from app.db import SessionLocal as _Sess
    from app.models import OpsUser as _Ops
    _db = _Sess()
    published = {_cfg.founder_email} | {
        row.email for row in _db.query(_Ops).all() if row.email
    }
    _db.close()
    # Whoever the outbound envelope is from can be replied to, by definition.
    published.add((_cfg.sending_from or "support@").split("<")[-1].strip(" >"))
    check("we publish at least the two ops addresses", len(published) >= 2, str(sorted(published)))

    unroutable = sorted(
        addr for addr in published
        if not any(addr.lower().startswith(p) for p in prefixes)
    )
    check("every published address is one the worker would let through",
          not unroutable, f"dropped in Cloudflare, silently: {unroutable}")
    check("and a reply token still routes, which is most inbound mail",
          any(p == "reply+" for p in prefixes), str(prefixes))

    print("\n== a real dealer platform, parsed from its own markup ==")
    # The first adapter written against a real site rather than the JSON-LD
    # fixture. Dealer Car Search puts the whole vehicle on each search-result
    # card, so 481 vehicles cost five list pages rather than 481 detail
    # fetches -- which is the difference between a polite crawl and one a
    # dealer would be right to block.
    from app.ingest.extract import list_adapter_for, valid_vin
    from app.ingest.sites.dealercarsearch import DealerCarSearch
    import app.ingest.sites  # noqa: F401 -- registers the adapter

    dcs_html = pathlib.Path(
        "backend/app/ingest/fixtures/dealercarsearch_list.html"
    ).read_text()
    picked = list_adapter_for(dcs_html, "https://example.invalid/newandusedcars")
    check("a Dealer Car Search listing page is recognised",
          picked is not None and picked.name == "dealercarsearch",
          picked.name if picked else "nothing matched")

    cards = DealerCarSearch().parse_list(dcs_html, "https://example.invalid/newandusedcars")
    check("every card on it yields a vehicle", len(cards) == 3, str(len(cards)))
    first = cards[0]
    check("with the fields Liner cannot talk about a car without",
          (first.year, first.make, first.model, first.price, first.mileage)
          == (2018, "Dodge", "Challenger", 110500, 9029),
          f"{first.year} {first.make} {first.model} {first.price} {first.mileage}")
    check("and a VIN that is a real VIN", valid_vin(first.vin), first.vin)
    check("the photo comes from data-src, not the lazy-load placeholder",
          first.photo_url.startswith("https://imagescdn."), first.photo_url[:40])

    # The price label differs per store -- "Craig's Best Price" against
    # "Internet Value Price" -- so the class is what is read. Matching the
    # label would break the day a dealer renames their own pricing.
    check("a differently-labelled price is still read",
          cards[1].price == 83500, str(cards[1].price))

    # A two-word make split on the first space is a Rover, which is not a car.
    rover = DealerCarSearch().parse_list(
        '<div class="v-card"><div class="v-card-body"><a href="/vdp/1/x"></a>'
        '<h4 class="vehicleTitle"> 2025 Land Rover Range Rover Sport</h4>'
        '<span class="vehicleTrim">P400 Dynamic</span>'
        '<p class="v-feature i13r_optVin"><label>VIN:</label>&nbsp;'
        'SAL1P9EU9SA601106</p></div></div>', "https://example.invalid/")[0]
    check("a two-word make survives the split",
          (rover.make, rover.model) == ("Land Rover", "Range Rover Sport"),
          f"{rover.make} / {rover.model}")

    # One DCS site can list several stores, each card carrying its own dealer
    # id -- and this app holds exactly one dealership. Without the filter
    # Liner offers a buyer a car two hours from the showroom it says it is in.
    one_lot = DealerCarSearch(dealer_id="1123").parse_list(dcs_html, "https://example.invalid/")
    check("another store's stock is left on another store's forecourt",
          len(one_lot) == 2 and all(c.raw["dealer_id"] == "1123" for c in one_lot),
          f"{len(one_lot)} of {len(cards)} kept")

    check("paging asks for the next page and the biggest page size",
          "page=2" in DealerCarSearch().page_url("https://x/n", 2, "Page: 1 of 21")
          and "pagesize=100" in DealerCarSearch().page_url("https://x/n", 2, "Page: 1 of 21"))
    check("and stops when the pager says there is no next page",
          DealerCarSearch().page_url("https://x/n", 22, "Page: 21 of 21 (481 vehicles)") is None)

    # The rung it sits beside must not be disturbed: the fixture site emits
    # JSON-LD and is not a DCS site.
    riverside = sorted(pathlib.Path("backend/fixtures/sites/riverside").rglob("*.html"))
    if riverside:
        other = riverside[-1].read_text()
        check("and it does not claim a site that is not its platform",
              list_adapter_for(other, "http://x/") is None)

    # Pagination is a request the site has to honour, and a site that does not
    # hands back page one again: an unknown pagesize, a filter that resets, a
    # proxy serving a cache. Nothing in that response says it is a repeat -- it
    # is HTTP 200 full of cars -- so the crawl read page one twenty-one times,
    # reported 481 vehicles, and handed the diff forty-two copies of two cars.
    # Measured against a server that ignores query strings, not reasoned.
    from app.ingest.pipeline import crawl_list as _crawl

    class _Stuck:
        """A site whose pager always answers with the same page."""

        def __init__(self, body):
            self.body = body
            self.calls = 0

        def get(self, url, timeout=0):
            self.calls += 1
            return SimpleNamespace(text=self.body, status_code=200)

    stuck = _Stuck(dcs_html)
    was_rate = cfg.scraper_rate_limit
    try:
        cfg.scraper_rate_limit = 1000.0  # do not sleep through the check
        found, problems, _method = _crawl(
            stuck, DealerCarSearch("1123"), "https://example.invalid/n", dcs_html
        )
    finally:
        cfg.scraper_rate_limit = was_rate

    check("a pager that never advances ends the crawl instead of looping",
          stuck.calls <= 2, f"{stuck.calls} extra page fetch(es)")
    check("and every car comes back once, not once per repeated page",
          len(found) == len({car.vin for car in found}), f"{len(found)} rows")
    check("and the repeat is reported, not swallowed",
          any("not advancing" in p["error"] for p in problems), str(problems)[:80])

    # robots.txt returns "allowed" on a timeout, a 404 and a 500 alike -- a
    # site that never answered has not refused, which is the right default and
    # a lie to *report* as "robots.txt allows this path". It made a crawl whose
    # very first request had already timed out print a clean permission check,
    # then fail one step later looking like a different problem.
    from app.ingest.pipeline import robots_verdict as _verdict

    class _Dead:
        def get(self, url, timeout=0):
            raise httpx.ConnectTimeout("timed out")

    class _Open:
        def get(self, url, timeout=0):
            return SimpleNamespace(status_code=200, text="User-agent: *\nAllow: /")

    class _Shut:
        def get(self, url, timeout=0):
            return SimpleNamespace(status_code=200, text="User-agent: *\nDisallow: /")

    allowed, why = _verdict(_Dead(), "https://x.invalid", "/n")
    check("an unreachable robots.txt is reported as unreachable, not as permission",
          allowed and "could not be read" in why, why)
    allowed, why = _verdict(_Open(), "https://x.invalid", "/n")
    check("and a real allow still reads as one", allowed and "allows" in why, why)
    allowed, why = _verdict(_Shut(), "https://x.invalid", "/n")
    check("and a refusal is still obeyed", not allowed, why)

    # `make ingest` chooses one fix from the layer that failed. A verdict with
    # no entry in that table would KeyError at exactly the worst moment -- in
    # front of somebody whose crawl has just died -- so the two lists have to
    # agree.
    ingest_src = pathlib.Path("scripts/ingest.py").read_text()
    verdicts = set(re.findall(r'return lines, "(\w+)"', ingest_src))
    verdicts |= set(re.findall(r'verdict = "(\w+)"', ingest_src))
    handled = set(re.findall(r'^\s{16}"(\w+)": \[', ingest_src, re.MULTILINE))
    check("every connection verdict has a fix to offer", verdicts <= handled,
          f"unhandled: {sorted(verdicts - handled)}")
    check("and the diagnosis says what a TCP failure rules out",
          "CAPTCHA" in ingest_src and "responses" in ingest_src)

    print("\n== somebody writing in becomes somebody ==")
    # A person writing to sales@ used the door the dealership publishes. Left
    # as a receipt they are visible only on a diagnostics tab, so the one
    # contact nobody expected is also the one nobody works.
    #
    # A machine writing to it is not a buyer. The buyer list is the one list
    # here that has to mean exactly one thing, and a lead invented from a
    # newsletter is a name in every assignment picker and a row in every queue.
    from app.email_intake import (
        automated_reason, display_name, just_the_reply, sender_address, signature_name,
    )

    who = f"buyer.{stamp}@example.invalid"
    inbound({
        "messageId": f"<first-{stamp}@mail.example>",
        "from": f'"Robin Vance" <{who}>', "to": "sales@example.invalid",
        "subject": "2019 Silverado",
        "text": "Is the Silverado still there?\n\nThanks,\nRobin",
    })
    settled(f"<first-{stamp}@mail.example>")
    made = call("GET", f"/api/leads?q={quote(who)}")
    rows = [r for r in (made.get("leads") or made) if r.get("email") == who]
    check("a person writing to a published address becomes a buyer",
          len(rows) == 1, f"{len(rows)} lead(s)")

    # **The shape the deployed Worker actually posts.** `from` is
    # `message.from`, the *envelope* sender -- a bare address, because a mail
    # server does not put a display name in an envelope. The parsed header's
    # name arrives separately as `fromName`, and nothing read it: so every real
    # buyer arrived "Unnamed buyer" while the tests, which put a display name
    # in `from`, passed. Driven here in the Worker's shape for that reason.
    named = f"jamie.{stamp}@example.invalid"
    inbound({
        "messageId": f"<worker-shape-{stamp}@mail>",
        "from": named, "fromAddress": named, "fromName": "Jamie Okafor",
        "to": "sales@example.invalid", "subject": "Silverado",
        "text": "Is it still there?",
    })
    settled(f"<worker-shape-{stamp}@mail>")
    got = [r for r in call("GET", "/api/leads?limit=300")["leads"]
           if r["email"] == named]
    check("a buyer arrives with the name their mail client sent",
          got and got[0]["name"] == "Jamie Okafor",
          got[0]["name"] if got else "(no lead)")

    # And when the envelope carries none, the signature is better than
    # "Unnamed buyer" -- which is a rep opening the message to find what the
    # row could have told them. Still a guess, and still recorded as one.
    signed_addr = f"morgan.{stamp}@example.invalid"
    inbound({
        "messageId": f"<signed-{stamp}@mail>",
        "from": signed_addr, "fromAddress": signed_addr, "fromName": "",
        "to": "sales@example.invalid", "subject": "Trade-in",
        "text": "What would you give me for a 2015 Civic?\n\nThanks,\nMorgan Reed",
    })
    settled(f"<signed-{stamp}@mail>")
    signer = next(r for r in call("GET", "/api/leads?limit=300")["leads"]
                  if r["email"] == signed_addr)
    check("and one who only signed their message is named from that",
          signer["name"] == "Morgan Reed", signer["name"])
    check("with the guess still marked a guess, where provenance can live",
          any(f["key"] == "signed_name" and not f["verified"]
              for f in signer.get("captured_fields") or []),
          str(signer.get("captured_fields")))

    # **A name arrives late, and the row has to take it.** The ladder ran only
    # where a lead was *minted*, so a buyer resolved by token or by address --
    # which is every returning one -- never went near it. Anyone already on
    # file without a name stayed unnamed for good, however many messages they
    # sent afterwards carrying a perfectly good one. Their first message here
    # has no display name and nothing to sign, so the row starts blank exactly
    # the way a real one did.
    late = f"late.{stamp}@example.invalid"
    inbound({
        "messageId": f"<late-blank-{stamp}@mail>",
        "from": late, "fromAddress": late, "fromName": "",
        "to": "sales@example.invalid", "subject": "Hours",
        "text": "are you open sunday",
    })
    settled(f"<late-blank-{stamp}@mail>")
    blank = next(r for r in call("GET", "/api/leads?limit=300")["leads"]
                 if r["email"] == late)
    check("a buyer whose first mail carried no name starts unnamed",
          not blank["has_name"], blank["name"])
    inbound({
        "messageId": f"<late-named-{stamp}@mail>",
        "from": late, "fromAddress": late, "fromName": "Sam Okonkwo",
        "to": "sales@example.invalid", "subject": "Re: Hours",
        "text": "thanks",
    })
    settled(f"<late-named-{stamp}@mail>")
    filled = next(r for r in call("GET", "/api/leads?limit=300")["leads"]
                  if r["email"] == late)
    check("and a later message carrying one fills the blank",
          filled["name"] == "Sam Okonkwo", filled["name"])
    check("without minting a second buyer to hang it on",
          len([r for r in call("GET", "/api/leads?limit=300")["leads"]
               if r["email"] == late]) == 1)

    # **And never the other way.** A name already on the row came from
    # somewhere with more authority -- a booking, a rep who typed it, a lead
    # document -- while a display name is free text the sender's own client
    # will put anything in. Renaming a buyer a rep has confirmed is how
    # somebody ends up on the phone to the wrong name.
    inbound({
        "messageId": f"<late-rename-{stamp}@mail>",
        "from": late, "fromAddress": late, "fromName": "sam's work laptop",
        "to": "sales@example.invalid", "subject": "Re: Hours",
        "text": "one more thing",
    })
    settled(f"<late-rename-{stamp}@mail>")
    kept = next(r for r in call("GET", "/api/leads?limit=300")["leads"]
                if r["email"] == late)
    check("but a name already on the row is never overwritten by a later one",
          kept["name"] == "Sam Okonkwo", kept["name"])
    if rows:
        check("named from the envelope, which is a fact rather than a guess",
              rows[0]["name"] == "Robin Vance", rows[0]["name"])
        check("and email counts as a channel they have used",
              rows[0].get("channels") == ["email"], str(rows[0].get("channels")))

    # Twice is one buyer. `_resolve` matched the whole `Name <addr>` header
    # against `leads.email` and so never hit for a sender with a display name,
    # which is most real mail -- invisible while the outcome was "unresolved",
    # and a duplicate buyer the moment an unmatched delivery started minting
    # one. Both sites parse the address now, and this is what checks it.
    inbound({
        "messageId": f"<second-{stamp}@mail.example>",
        "from": f'"Robin Vance" <{who}>', "to": "sales@example.invalid",
        "subject": "Re: 2019 Silverado", "text": "Any movement on the price?",
    })
    receipt = settled(f"<second-{stamp}@mail.example>")
    again = call("GET", f"/api/leads?q={quote(who)}")
    check("and writing again is the same buyer, not a second one",
          len([r for r in (again.get("leads") or again) if r.get("email") == who]) == 1)
    check("filed by the address, and the receipt says which rule did it",
          receipt.get("matched_by") == "from_address", str(receipt.get("matched_by")))

    for label, sender, headers, body in [
        ("a mailing list", "News <news@vendor.example>",
         {"List-Unsubscribe": "<https://vendor.example/u>"}, "Top ten SUVs!"),
        ("an out-of-office", "Someone <someone@example.invalid>",
         {"Auto-Submitted": "auto-replied"}, "I am away until Monday."),
        ("a no-reply robot that declares nothing", "no-reply@billing.example",
         {}, "Invoice 4021."),
    ]:
        mid = f"<bot-{label.split()[-1]}-{stamp}@mail.example>"
        inbound({
            "messageId": mid, "from": sender, "to": "sales@example.invalid",
            "subject": "x", "text": body, "headers": headers,
        })
        got = settled(mid)
        check(f"{label} is kept as a receipt and makes no buyer",
              got.get("outcome") == "unresolved" and not got.get("lead_id"),
              f"{got.get('outcome')} / lead={bool(got.get('lead_id'))}")
        check(f"and the receipt says why -- {label}",
              "no lead was created because" in (got.get("detail") or ""),
              (got.get("detail") or "")[:70])

    # Headers first, then the address, then shape. A header is the sender
    # declaring itself a machine, and honouring it stops a vacation responder
    # on turn one -- where a cooldown only slows it to forty-eight real emails
    # a day, forever.
    check("a header outranks an address that looks fine",
          automated_reason("Real Person <a@b.example>",
                           {"Precedence": "bulk"}, "hello") != "")
    check("and a plain person with no headers at all is let through",
          automated_reason("Real Person <a@b.example>", {}, "hello") == "")
    check("a suffixed robot mailbox is still a robot mailbox",
          automated_reason("no-reply+alerts@b.example", {}, "hi") != "")
    check("an empty message is nobody to reply to",
          automated_reason("a@b.example", {}, "   ") != "")

    check("the address is read out of the envelope, never compared whole",
          sender_address('"Robin Vance" <Robin@Example.Invalid>') == "robin@example.invalid",
          sender_address('"Robin Vance" <Robin@Example.Invalid>'))
    check("and a display name that is only the address says nothing",
          display_name("<a@b.example>") == "" and display_name('"A B" <a@b.example>') == "A B")

    # The signature is a guess, so it is a captured field with provenance
    # `inferred` and never the lead's own name. Prose cannot carry provenance,
    # and a guessed name asserted as fact is one a rep repeats on the phone.
    check("a name signed under a sign-off is found",
          signature_name("Is it there?\n\nThanks,\nDana Public") == "Dana Public",
          signature_name("Is it there?\n\nThanks,\nDana Public"))
    check("a phone footer is not a name",
          signature_name("Is it there?\n\nSent from my iPhone") == "",
          signature_name("Is it there?\n\nSent from my iPhone"))
    check("and a one-line message is the message, not a signature",
          signature_name("Robin") == "")

    # Some people answer inside the quote. Trimming to nothing would delete the
    # only thing they said, so it keeps the whole body instead -- the same rule
    # `_is_noise` follows on a call, for the same reason.
    quoted = "On Mon 1 Sep 2026 Liner wrote:\n> Are you still interested?\n> Let me know."
    check("a reply that is nothing but quote keeps its body rather than vanishing",
          just_the_reply(quoted).strip() != "")
    check("and an ordinary reply loses the thread it quoted back",
          just_the_reply("Yes please.\n\nOn Mon 1 Sep 2026 Liner wrote:\n> hello\n")
          == "Yes please.")

    print("\n== Liner answers an email, with the brakes in front of it ==")
    # The same loop, the same eight tools and the same guards as chat, given an
    # addendum about being in an inbox. A second copy of the loop is how one
    # channel quietly stops running the guards.
    from app import email_reply as replier
    from app import flags as _flags
    # Aliased: `say` is already the chat sender in this file.
    from app.agent.fake_provider import FakeProvider
    from app.agent.fake_provider import say as scripted
    from app.agent.prompts import EMAIL_ADDENDUM, build_system_prompt
    from app.api.settings import live_settings as _live
    from app.config import settings as _cfg
    from app.db import SessionLocal as _ReplySession
    from app.models import (
        Conversation as _Convo, Dealership as _Dealer, InboundEmail as _Receipt,
        Lead as _L, Message as _Msg, Outreach as _Out,
    )

    with _ReplySession() as _db:
        dealer = _db.query(_Dealer).first()
        row = _live(_db)
        prompts = {
            ch: build_system_prompt(_db, dealer, row, channel=ch)
            for ch in ("chat", "voice", "email")
        }
        check("email gets its own addendum, appended like voice's",
              "BY EMAIL" in prompts["email"] and "BY EMAIL" not in prompts["chat"])
        for ch, text in prompts.items():
            check(f"and {ch} is given exactly one channel addendum",
                  sum(m in text for m in
                      ("BY EMAIL", "ON A PHONE CALL", "ON A SCREEN")) == 1)
        # Email is a third channel on **one** prompt, not a prompt of its own.
        # Two full prompts is how the price rule ends up stricter on one
        # channel than another, so the brief and the operating rules have to be
        # in this one exactly as they are in the other two.
        check("email carries the same brief and rules as chat and voice",
              all(mark in prompts["email"] for mark in
                  ("WHAT YOU ARE DOING", "HOW THIS PLACE ACTUALLY WORKS",
                   "EVERY CAR FACT COMES FROM A TOOL RESULT")))
        check("and the addendum stays short enough to reread every turn",
              len(EMAIL_ADDENDUM) < 1500, f"{len(EMAIL_ADDENDUM)} chars")
        check("it says not to quote their message back at them",
              "Do not quote their message back" in EMAIL_ADDENDUM)
        check("and to hand over rather than go quiet",
              "escalate_to_human" in EMAIL_ADDENDUM)

        # Over the cap, Liner does not answer badly -- it hands the message to
        # a person. Truncating and answering anyway means confidently replying
        # to the top of something whose question was at the bottom.
        body, refused = replier.readable("Is the Silverado still there?")
        check("an ordinary message reaches the model", body and not refused)
        _, refused = replier.readable("x" * (replier.MAX_BODY_CHARS + 1))
        check("a very long one is handed to a person instead of half-answered",
              "a person should read this one" in refused, refused[:60])
        _, refused = replier.readable("   ")
        check("and one with nothing in it is not answered at all", bool(refused))

        was_env, was_cool = _cfg.email_agent, _cfg.email_reply_cooldown_minutes
        try:
            _cfg.email_agent = True
            _cfg.email_reply_cooldown_minutes = 0
            _flags.set(_db, "email_agent", "on", reason="smoke")

            buyer = _L(name="Pat Quinn", email=f"pat.{stamp}@example.invalid",
                       phone="", source="email")
            _db.add(buyer)
            _db.commit()
            envelope = _Receipt(
                outcome="accepted", message_id=f"<answer-{stamp}@mail>",
                from_address=f'"Pat Quinn" <{buyer.email}>',
                to_address="sales@example.invalid", subject="2019 Silverado",
                body="Is the Silverado still there?", lead_id=buyer.id,
            )
            theirs = _Out(
                lead_id=buyer.id, channel="email", direction="in", kind="reply",
                to_address=buyer.email, subject="2019 Silverado",
                body="Is the Silverado still there?", provider="inbound",
                provider_message_id=f"<answer-{stamp}@mail>", status="sent",
                sent_at=utcnow_local(),
            )
            _db.add_all([envelope, theirs])
            _db.commit()

            spoke = replier.answer(
                _db, envelope, buyer, theirs,
                provider=FakeProvider([scripted("Happy to help -- when suits you?")]),
            )
            check("Liner answers, and the reply is a real outreach row",
                  spoke["sent"] and spoke.get("outreach_id"), str(spoke.get("reason")))

            thread = _db.query(_Convo).filter_by(
                lead_id=buyer.id, channel="email").one()
            check("a conversation exists from the first reply, not the third",
                  bool(thread.id))
            check("so Take over, pause and escalation are there from turn one",
                  hasattr(thread, "agent_paused"))
            said = [m.role for m in _db.query(_Msg).filter_by(
                conversation_id=thread.id).all()]
            check("and both sides are message rows, like any other channel",
                  said == ["buyer", "liner"], str(said))

            reply_row = _db.query(_Out).filter_by(
                lead_id=buyer.id, direction="out").order_by(
                _Out.created_at.desc()).first()
            check("the send carries no user id, which is the whole author test",
                  reply_row.sent_by_user_id is None)
            check("it threads under the message it answers",
                  reply_row.in_reply_to == f"<answer-{stamp}@mail>",
                  str(reply_row.in_reply_to))
            check("and carries a token so their answer comes back here",
                  bool(reply_row.reply_token))
            check("the subject is Re:, not a second thread in their inbox",
                  reply_row.subject.lower().startswith("re:"), reply_row.subject)

            # A rep holding the thread is holding it on every channel, and it
            # is re-checked immediately before the wire: a model round trip
            # takes seconds, and somebody pressing Take over during one must
            # not be overtaken by a message already in flight.
            thread.agent_paused = True
            _db.commit()
            held = replier.answer(
                _db, envelope, buyer, theirs,
                provider=FakeProvider([scripted("second answer")]),
            )
            check("a rep who has taken over stops Liner answering again",
                  not held["sent"] and held["reason"] == "rep_holding", held["reason"])
            thread.agent_paused = False
            _db.commit()

            # The guards are the point of reusing the loop. An availability
            # claim with no lookup this turn is caught on email exactly as it
            # is on a screen.
            invented = replier.answer(
                _db, envelope, buyer, theirs,
                provider=FakeProvider([scripted("Yes, that one is still available.")]),
            )
            body = (invented.get("body") or "")
            check("and an unsourced claim is caught on email as on a screen",
                  invented["sent"] and "still available" not in body, body[:60])
        finally:
            _cfg.email_agent, _cfg.email_reply_cooldown_minutes = was_env, was_cool
            _flags.set(_db, "email_agent", "off", reason="smoke reset")

    print("\n== every reply waits, and a person can get there first ==")
    # **Every reply waits, including the first.** Answering three seconds after
    # a buyer wrote is the most robotic thing a mailbox can do -- and the wait
    # buys something besides: a window in which a rep reads the message and
    # takes the thread over before anything goes out on its own.
    from datetime import timedelta as _delta

    from app import email_replies as _ticker
    from app import email_reply as _replier
    from app import flags as _f
    from app.config import settings as _s
    from app.db import SessionLocal as _WaitSession
    from app.models import (
        EmailReplyDue as _Due, InboundEmail as _In, Lead as _Buyer,
        Outreach as _Sent, User as _Staff,
    )

    with _WaitSession() as _db:
        keep = (_s.email_agent, _s.email_reply_cooldown_minutes)
        try:
            _s.email_agent = True
            _s.email_reply_cooldown_minutes = 5
            _f.set(_db, "email_agent", "on", reason="smoke")

            def arrives(tag):
                who = _Buyer(name="", email=f"{tag}.{stamp}@example.invalid",
                             phone="", source="email")
                _db.add(who)
                _db.commit()
                envelope = _In(
                    outcome="accepted", message_id=f"<{tag}-{stamp}@mail>",
                    from_address=who.email, to_address="sales@example.invalid",
                    subject="Silverado", body="Is it still there?", lead_id=who.id,
                )
                theirs = _Sent(
                    lead_id=who.id, channel="email", direction="in", kind="reply",
                    to_address=who.email, subject="Silverado",
                    body="Is it still there?", provider="inbound",
                    status="sent", sent_at=utcnow_local(),
                )
                _db.add_all([envelope, theirs])
                _db.commit()
                return who, envelope, theirs

            buyer, envelope, theirs = arrives("waits")
            queued = _replier.schedule(_db, envelope, buyer, theirs)
            check("a message is queued rather than answered on the spot",
                  queued["queued"], str(queued.get("reason")))
            check("and nothing has gone out yet",
                  _db.query(_Sent).filter_by(
                      lead_id=buyer.id, direction="out").count() == 0)
            check("draining before it is due sends nothing",
                  _ticker.drain(provider=FakeProvider([scripted("early")])) == [])

            row = _db.query(_Due).filter_by(lead_id=buyer.id).one()
            row.due_at = utcnow_local() - _delta(seconds=1)
            _db.commit()
            fired = _ticker.drain(
                provider=FakeProvider([scripted("Happy to help -- when suits you?")]))
            check("once the clock passes, the reply goes",
                  fired and fired[0].get("sent"), str(fired))
            _db.refresh(row)
            check("and the queue row records that it went", row.state == "sent", row.state)

            # The whole point of waiting. A rep reading the message and
            # answering it inside the window is the ordinary outcome, not a
            # failure -- and Liner must not add a second email behind them.
            other, envelope2, theirs2 = arrives("beaten")
            _replier.schedule(_db, envelope2, other, theirs2)
            rep = _db.query(_Staff).first()
            _db.add(_Sent(
                lead_id=other.id, channel="email", direction="out", kind="reply",
                to_address=other.email, subject="Re: Silverado", body="Yes it is.",
                status="sent", sent_at=utcnow_local(), sent_by_user_id=rep.id,
            ))
            _db.commit()
            waiting = _db.query(_Due).filter_by(lead_id=other.id).one()
            waiting.due_at = utcnow_local() - _delta(seconds=1)
            _db.commit()
            _ticker.drain(provider=FakeProvider([scripted("should never send")]))
            _db.refresh(waiting)
            check("a rep answering inside the window cancels the queued reply",
                  waiting.state == "skipped" and "person answered" in waiting.detail.lower(),
                  f"{waiting.state}: {waiting.detail}")
            check("so the buyer gets one email, not two",
                  _db.query(_Sent).filter_by(
                      lead_id=other.id, direction="out").count() == 1)

            # The ticker only looks when the agent is on, so an ordinary
            # deployment pays one indexed query and nothing else.
            check("the wait is the one setting, not a second number",
                  _s.email_reply_cooldown_minutes == 5)
        finally:
            _s.email_agent, _s.email_reply_cooldown_minutes = keep
            _f.set(_db, "email_agent", "off", reason="smoke reset")

    print("\n== the brakes, built before there is anything to stop ==")
    # Nothing answers email yet. These ship first deliberately: there must
    # never be a build where Liner can send mail on its own and cannot be
    # stopped, and a brake first exercised on the day it is needed is one
    # nobody has ever seen work.
    from app import email_agent as agent
    from app import flags as runtime_flags
    from app.config import settings as cfg_settings
    from app.db import SessionLocal as _BrakeSession
    from app.models import Lead as _Lead, Outreach as _Outreach

    state = call("GET", "/api/email/agent")
    check("email replies are off unless a deployment turned them on",
          not state["on"] and not state["allowed_by_env"],
          f"on={state['on']} env={state['allowed_by_env']}")
    check("and the refusal names which of the two switches is shut",
          state["reason"] == "off_in_env" and "EMAIL_AGENT" in state["detail"],
          state["reason"])
    check("the flag defaults off as well, so neither alone opens the door",
          state["flag"] == "off", state["flag"])

    # **There has to be a model, and saying so is the whole point.** Without
    # this check `run_turn` reached straight for a provider that is not
    # configured, `NotConfigured` came back up through a background task, and
    # the result was indistinguishable from a buyer who had not written -- the
    # exact failure the receipts exist to prevent, arriving through the one
    # path that had no receipt for it.
    with _BrakeSession() as _db:
        was = cfg_settings.email_agent
        try:
            cfg_settings.email_agent = True
            runtime_flags.set(_db, "email_agent", "on", reason="smoke")
            stub_verdict = agent.enabled(_db)
            check("with no model there is no reply, and it says which setting",
                  not stub_verdict.allowed and stub_verdict.reason == "no_model"
                  and "LLM_MODE" in stub_verdict.detail,
                  stub_verdict.reason)
        finally:
            cfg_settings.email_agent = was
            runtime_flags.set(_db, "email_agent", "off", reason="smoke reset")
    check("and every refusal is listed where a person would look for it",
          "declined" in state, str(sorted(state)))

    # The dashboard switch takes effect on the next request, not the next
    # deploy. That is the whole reason it is a table and not a `.env` line: it
    # is reached for at three in the morning, from a phone.
    thrown = call("POST", "/api/email/agent", {"value": "on", "reason": "smoke"})
    check("a rep can throw the switch without a restart",
          thrown["flag"] == "on", thrown["flag"])
    check("but `.env` still has the final word, and says so",
          not thrown["on"] and thrown["reason"] == "off_in_env", thrown["reason"])
    call("POST", "/api/email/agent", {"value": "off", "reason": "smoke reset"})

    # **A switch with no way to throw it is worse than no switch.** This
    # endpoint shipped a phase before any control for it, and the runtime flag
    # defaults off -- so a deployment that set `EMAIL_AGENT=true` and
    # `LLM_MODE=live` correctly, and did everything the runbook asked, was
    # never going to get a reply, with nothing on any screen saying why. The
    # setting said the feature was on and the product silently disagreed.
    # Read out of the page for the same reason `SPA_PREFIXES` is: nothing else
    # here can tell a control that exists from one that was only ever
    # described in a plan.
    _email_page = pathlib.Path("frontend/src/routes/EmailSetup.tsx").read_text()
    check("the dashboard has the switch, not just the endpoint behind it",
          "'/api/email/agent'" in _email_page and "Switch on" in _email_page,
          "EmailSetup.tsx does not post to /api/email/agent")
    check("and it names all three things that have to be true separately",
          all(k in _email_page for k in ("allowed_by_env", "live_model", "state.flag")),
          "the card collapses them into one 'off'")

    with _BrakeSession() as _db:
        was_env = cfg_settings.email_agent
        was_cool = cfg_settings.email_reply_cooldown_minutes
        was_ceiling = cfg_settings.email_replies_per_hour
        try:
            cfg_settings.email_agent = True
            runtime_flags.set(_db, "email_agent", "on", reason="smoke")
            fresh = _Lead(name="Brake Test", email=f"brake.{stamp}@example.invalid",
                          phone="", source="email")
            _db.add(fresh)
            _db.commit()

            check("with both switches on, a first message may be answered",
                  agent.may_reply(_db, fresh, has_provider=True).allowed)

            # A header is the sender declaring itself a machine, and it is
            # checked before any timer -- a cooldown does not *stop* a loop.
            robot = agent.may_reply(
                _db, fresh, automated="list-id header says so", has_provider=True
            )
            check("a machine is never answered, whatever the clock says",
                  not robot.allowed and robot.reason == "automated", robot.reason)

            # A rep's reply is the answer. Liner adding a second one minutes
            # later gives the buyer two emails from one dealership.
            _db.add(_Outreach(
                lead_id=fresh.id, channel="email", direction="out", kind="reply",
                to_address=fresh.email, subject="Re:", body="A person wrote this.",
                status="sent", sent_at=utcnow_local(), sent_by_user_id=me_id,
            ))
            _db.commit()
            person = agent.may_reply(_db, fresh, has_provider=True)
            check("a person having answered stops Liner answering again",
                  not person.allowed and person.reason == "person_answered",
                  person.reason)

            # One clock, whoever wrote. A rep's send satisfies the buyer's
            # message exactly as Liner's does, so releasing a thread must not
            # fire an immediate second answer to something already handled.
            cfg_settings.email_reply_cooldown_minutes = 0
            check("and with the cooldown elapsed it may answer again",
                  agent.may_reply(_db, fresh, has_provider=True).allowed)
            cfg_settings.email_reply_cooldown_minutes = 60

            liner = _Outreach(
                lead_id=fresh.id, channel="email", direction="out", kind="reply",
                to_address=fresh.email, subject="Re:", body="Liner wrote this.",
                status="sent", sent_at=utcnow_local(), sent_by_user_id=None,
            )
            _db.add(liner)
            _db.commit()
            soon = agent.may_reply(_db, fresh, has_provider=True)
            check("its own last reply holds the same clock",
                  not soon.allowed and soon.reason == "cooldown", soon.reason)
            check("and the refusal names the setting that moves it",
                  "EMAIL_REPLY_COOLDOWN_MINUTES" in soon.detail, soon.detail[:60])

            # Per correspondent stops one loop. A spam run across five hundred
            # addresses walks past it, because every one is a first contact --
            # so there is a ceiling across all of them, and breaching it throws
            # the switch rather than waiting for somebody to wake up.
            cfg_settings.email_reply_cooldown_minutes = 0
            cfg_settings.email_replies_per_hour = 1
            tripped = agent.may_reply(_db, fresh, has_provider=True)
            check("an hourly ceiling across every correspondent stops a spam run",
                  not tripped.allowed and tripped.reason == "hourly_ceiling",
                  tripped.reason)
            check("and it throws the kill switch rather than refusing one message",
                  runtime_flags.get(_db, "email_agent") == "off",
                  runtime_flags.get(_db, "email_agent"))
            check("recording that a machine did it, not a person",
                  "automatically" in next(
                      f["reason"] for f in runtime_flags.all_flags(_db)
                      if f["key"] == "email_agent"
                  ))
        finally:
            cfg_settings.email_agent = was_env
            cfg_settings.email_reply_cooldown_minutes = was_cool
            cfg_settings.email_replies_per_hour = was_ceiling
            runtime_flags.set(_db, "email_agent", "off", reason="smoke reset")

    check("an unknown flag is refused rather than quietly stored",
          _raises_keyerror(runtime_flags))

    # The Worker has to send what the loop-breaker reads, and only that. A full
    # header dump is somebody's routing metadata travelling through our webhook
    # for no reason.
    worker_src = pathlib.Path(
        "backend/app/integrations/email/worker/src/index.ts"
    ).read_text()
    for header in ("auto-submitted", "list-id", "list-unsubscribe", "precedence"):
        check(f"the worker forwards {header}", f'"{header}"' in worker_src)
    check("and it already forwards attachment names and sizes, never the bytes",
          "attachments:" in worker_src and "content?.byteLength" in worker_src)
    from app.email_intake import AUTOMATED_HEADERS

    forwarded = set(re.findall(r'^\t\t\t"([a-z-]+)",$', worker_src, re.MULTILINE))
    check("every header the backend checks is one the worker sends",
          set(AUTOMATED_HEADERS) <= forwarded,
          f"not sent: {sorted(set(AUTOMATED_HEADERS) - forwarded)}")

    print("\n== a rep answers, and a buyer's two addresses become one buyer ==")
    # The loop this whole feature is for: they write, a rep replies from the
    # buyer's own page, and the reply threads under what they sent rather than
    # opening a second conversation in their inbox.
    work = f"casey.{stamp}@work.invalid"
    home = f"casey.{stamp}@home.invalid"
    inbound({
        "messageId": f"<casey-a-{stamp}@mail>", "from": f'"Casey Lane" <{work}>',
        "to": "sales@example.invalid", "subject": "2019 Silverado",
        "text": "Is the Silverado still available?",
    })
    settled(f"<casey-a-{stamp}@mail>")
    casey = next(
        row for row in call("GET", "/api/leads?limit=300")["leads"]
        if row["email"] == work
    )
    their_mail = next(
        e for e in call("GET", f"/api/leads/{casey['id']}/timeline")["entries"]
        if e["kind"] == "outreach" and e["direction"] == "in"
    )
    answer = call("POST", "/api/email/compose", {
        "to": work, "subject": "Re: 2019 Silverado",
        "body": "Yes, it is. Want to come and see it this week?",
        "lead_id": casey["id"], "in_reply_to_outreach_id": their_mail["id"],
    })
    check("a rep can answer from the buyer's own page",
          answer["status"] == "sent" and answer["lead_id"] == casey["id"],
          f"{answer['status']} -> {answer['lead_id']}")
    counted = next(
        row for row in call("GET", "/api/email/threads?box=all")["threads"]
        if row["lead_id"] == casey["id"]
    )
    check("and answering is what clears the waiting badge and moves the counter",
          counted["exchanges"] == 1 and not counted["waiting"],
          f"{counted['exchanges']} exchange(s), waiting={counted['waiting']}")

    # `leads.email` is one column and a buyer is not. The join is a human act:
    # matching is email exact and phone by its last ten digits, and no rule
    # here can see that two addresses are one person.
    inbound({
        "messageId": f"<casey-b-{stamp}@mail>", "from": home,
        "to": "sales@example.invalid", "subject": "(nothing)", "text": "   ",
    })
    settled(f"<casey-b-{stamp}@mail>")
    linked = call("POST", f"/api/leads/{casey['id']}/addresses", {"address": home})
    check("linking a second address claims what they sent from it",
          linked["claimed"] == 1, f"claimed {linked['claimed']}")
    check("and the buyer page says which addresses are theirs",
          [a["address"] for a in linked["addresses"]] == [home],
          str([a["address"] for a in linked["addresses"]]))

    inbound({
        "messageId": f"<casey-c-{stamp}@mail>", "from": f'"Casey Lane" <{home}>',
        "to": "sales@example.invalid", "subject": "Re: Silverado",
        "text": "Thursday works for me.",
    })
    placed = settled(f"<casey-c-{stamp}@mail>")
    check("so their next message finds them with no further help",
          placed["lead_id"] == casey["id"], str(placed.get("matched_by")))
    check("and it did not mint a second buyer",
          len([r for r in call("GET", "/api/leads?limit=300")["leads"]
               if r["email"] in (work, home)]) == 1)
    thread = [
        e for e in call("GET", f"/api/leads/{casey['id']}/timeline")["entries"]
        if e["kind"] == "outreach"
    ]
    check("their whole exchange is on one timeline, in order",
          [e["direction"] for e in thread] == ["in", "out", "in", "in"],
          str([e["direction"] for e in thread]))

    # Taking an address that belongs to somebody would silently merge two
    # buyers, which is the one failure `app/matching.py` exists to prevent.
    taken = status_of("POST", f"/api/leads/{casey['id']}/addresses",
                      {"address": who})
    check("an address that belongs to somebody else is refused, not moved",
          taken[0] == 409, f"{taken[0]}: {taken[1][:60]}")
    check("and the refusal names who has it, so a rep can go and look",
          "already belongs to" in taken[1], taken[1][:70])

    # "Arrived as a lead document" was the only answer a buyer with no
    # conversation could get. True while ADF was the only way in, and a plain
    # untruth once an email could mint one.
    recap = call("GET", f"/api/leads/{casey['id']}/timeline")["recap"]
    check("and the recap says how they actually arrived",
          "by email" in recap and "lead document" not in recap, recap[:70])

    print("\n== one row per person, and one definition of a back and forth ==")
    # `/app/email` lists messages, which is what you want hunting one send.
    # This lists people, which is what you want deciding who to answer next --
    # four messages with one buyer are one relationship, not four things.
    #
    # The counter lives in `app/email_threads.py` and nowhere else, because it
    # decides two things read in different places: which tab a row is in, and
    # whether the badge says the buyer is waiting. Two copies is how a header
    # says 3 over a row that reads as 2.
    from app.email_threads import EXCHANGE_THRESHOLD, graduated, tally

    at = datetime(2026, 1, 1)
    def walk(*directions):
        return tally([(at + timedelta(minutes=i), d) for i, d in enumerate(directions)])

    check("an exchange is an inbound we answered, not a message",
          walk("in").exchanges == 0 and walk("in", "out").exchanges == 1,
          f'{walk("in").exchanges} then {walk("in", "out").exchanges}')
    check("a buyer who writes three times and gets one answer has had one",
          walk("in", "in", "in", "out").exchanges == 1,
          str(walk("in", "in", "in", "out").exchanges))
    check("and the two they are still owed are what waiting is for, not the count",
          walk("in", "in").waiting and not walk("in", "out").waiting)
    check("an outreach nobody replied to is not a conversation",
          walk("out", "out").exchanges == 0 and not walk("out").waiting)
    check("but one they answered and we answered back is",
          walk("out", "in", "out").exchanges == 1)
    check("three round trips graduates it", graduated(walk(*(["in", "out"] * 3))))
    check("two does not", not graduated(walk(*(["in", "out"] * 2))))
    check("and writing again afterwards leaves it graduated and waiting",
          graduated(walk(*(["in", "out"] * 3), "in"))
          and walk(*(["in", "out"] * 3), "in").waiting)
    check("the threshold is one constant, so the badge and the tab move together",
          EXCHANGE_THRESHOLD == 3, str(EXCHANGE_THRESHOLD))

    listed = call("GET", "/api/email/threads?box=all")
    check("the endpoint serves the same threshold it counted with",
          listed["threshold"] == EXCHANGE_THRESHOLD)
    by_key = {row["key"]: row for row in listed["threads"]}
    check("a buyer who wrote in is one row, however many messages they sent",
          len([r for r in listed["threads"] if r.get("address") == who]) == 1,
          str([r["key"] for r in listed["threads"] if r.get("address") == who]))
    check("every tab's count matches the rows it would show",
          all(
              listed["counts"][name]
              == len(call("GET", f"/api/email/threads?box={name}")["threads"])
              for name in ("open", "graduated", "waiting", "strangers")
          ),
          str(listed["counts"]))
    check("and Everyone is the whole set, not a tab beside the others",
          listed["counts"]["all"] == len(listed["threads"]))

    # A newsletter is not waiting for an answer. Since a person writing to a
    # published address becomes a buyer, everything left unplaced is a robot --
    # and flagging nine of those as owed a reply buries the one that is.
    strangers = call("GET", "/api/email/threads?box=strangers")["threads"]
    check("a delivery nobody could place never claims to be waiting",
          strangers and not any(r["waiting"] for r in strangers),
          f"{len(strangers)} stranger row(s)")
    check("and it says plainly that there is no buyer behind it",
          all(r["lead_id"] is None and r["kind"] == "stranger" for r in strangers))

    # Ours is ours. `support@` and `founder@` are Liner's boxes, and their
    # unplaced mail is listed at /ops -- a stranger writing to our support desk
    # is not a dealership's to read. The same realm rule `_lead_from` follows,
    # arriving through the list instead of through lead creation.
    ours_id = f"<realm-{stamp}@mail.example>"
    inbound({
        "messageId": ours_id, "from": f"cold.{stamp}@nowhere.invalid",
        "to": "support@example.invalid", "subject": f"For Liner {stamp}",
        "text": "Asking about your product.",
    })
    settled(ours_id)
    every = call("GET", "/api/email/threads?box=all")["threads"]
    check("mail to our own support desk is not in a dealership's list",
          not any(f"For Liner {stamp}" == r["last_subject"] for r in every))
    boxed = call("GET", "/api/email/messages?box=unmatched")["messages"]
    check("nor in its mailbox, which was listing every unplaced delivery",
          not any(m["subject"] == f"For Liner {stamp}" for m in boxed))

    print("\n== a timestamp on the wire says which kind it is ==")
    # ECMAScript parses a bare date-time as *browser-local*. `utcnow()` is naive
    # UTC, so `isoformat()` alone put an unmarked UTC instant on the wire and
    # every "5m ago" on the dashboard was computed against a clock shifted by
    # the viewer's own offset -- a reply that had just arrived read `5h ago` to
    # somebody at UTC+5. It read correctly on a machine set to UTC, which is
    # every box this has ever been developed on, which is why it survived.
    #
    # Two kinds, and only one is an instant. Appointment times are
    # dealership-local wall clock (`check_availability` builds them straight
    # out of `hours_json`), so 10:00 means ten at the showroom and a zone on it
    # would be a claim nobody can honour. They stay bare, deliberately.
    WALL_CLOCK = {"starts_at", "ends_at", "slot_at", "from", "to", "next_free_at"}
    TIMESTAMP = re.compile(r'"([a-z_]+)":\s*"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[^"]*)"')

    def unmarked(payload) -> list[str]:
        """Every `*_at`-shaped value carrying no zone, ignoring the wall clocks."""
        raw = json.dumps(payload)
        return sorted({
            f"{key}={value}"
            for key, value in TIMESTAMP.findall(raw)
            if key not in WALL_CLOCK and not value.endswith("Z") and "+" not in value[10:]
        })

    for path in ("/api/overview", "/api/conversations?limit=5", "/api/leads?limit=5",
                 "/api/appointments", "/api/inventory?limit=3", "/api/email/messages",
                 "/api/email/receipts"):
        payload = call("GET", path)
        bare = unmarked(payload)
        check(f"{path} marks every instant as UTC", not bare, str(bare[:3]))

    # And the wall-clock half is still bare, or the calendar shifts by the
    # viewer's offset the moment somebody opens it from another state.
    from app.schemas.serialize import iso as _iso, stamp as _stamp

    when = datetime(2026, 8, 30, 10, 0)
    check("an appointment time stays a wall clock, with no zone claimed",
          _iso(when) == "2026-08-30T10:00:00", _iso(when))
    check("and an instant is marked", _stamp(when) == "2026-08-30T10:00:00Z", _stamp(when))
    check("both answer None with None", _iso(None) is None and _stamp(None) is None)

    print("\n== a real dealer's own export, and the cars they will not price ==")
    # Craig and Landreth's whole group, exported by hand because their site
    # refuses this crawler below the HTTP layer, and committed so `make
    # reset-db` rebuilds the real lot with no network at all. It goes in
    # through the importer a dealer's own upload uses -- so what is checked
    # here is that importer against a real file, not a fixture written to suit
    # it.
    #
    # Everything below is offline and reads no rows: the Riverside fixture is
    # what is seeded when smoke runs, and a check that only passes on somebody
    # else's database is a check that does not run.
    from app.agent.tools import _hits, _vehicle_payload, _words, inquiry_url
    # Aliased: `pick` is already the rail chooser in this file.
    from app.ingest.csv_import import ALIASES, NEVER_IMPORT, pick as column
    from app.ingest.extract import usable_vin, valid_vin

    export = pathlib.Path("backend/fixtures/craigandlandreth/inventory.csv")
    rows = list(csv.DictReader(io.StringIO(export.read_text())))
    check("the committed export is the whole group, not one lot",
          len(rows) == 486, f"{len(rows)} rows")
    check("and every row carries the fields a card is drawn from",
          all(r["vin"] and r["year"] and r["make"] and r["photo_url"] and r["listing_url"]
              for r in rows))

    # Two of their cars are older than the 17-character VIN standard -- a 1978
    # Corvette and a 1979 Bronco. `valid_vin` is right for a page somebody
    # scraped and would have thrown away two real cars out of a file the
    # dealership exported itself.
    old = [r for r in rows if not valid_vin(r["vin"])]
    check("a pre-1981 VIN survives a file the dealer exported themselves",
          old and all(usable_vin(r["vin"]) for r in old),
          f"{len(old)}: {[r['vin'] for r in old]}")

    # What the dealership paid is on no row here, and the importer would drop
    # it if it were. Cost reaching a column is cost reaching search_inventory,
    # and from there the model.
    check("no cost column is anywhere near it",
          not (NEVER_IMPORT & {(k or '').lower() for k in rows[0]}),
          str(sorted(NEVER_IMPORT & {(k or '').lower() for k in rows[0]})))

    # The columns their export has that a DMS export does not. Without an alias
    # each the importer silently reads nothing and every card loses its photo.
    for column_name in ("photo_url", "listing_url", "location", "stock_number"):
        check(f"the importer reads their {column_name} column",
              column(rows[0], column_name) != "", f"aliases: {ALIASES.get(column_name)}")

    # 119 of 486 carry no price. That is a listing state the dealership chose,
    # and their own site answers it with an enquiry form at the same URL --
    # so the link is derived, never stored, and exists only where there is no
    # price to show.
    priced = SimpleNamespace(price=24995, listing_url="https://x.invalid/vdp/1/car",
                             raw_json=None, features_json="[]")
    unpriced = SimpleNamespace(price=None, listing_url="https://x.invalid/vdp/1/car",
                               raw_json=None, features_json="[]")
    check("a car with a price gets no enquiry link", inquiry_url(priced) == "")
    check("and a car without one gets the dealer's own form",
          inquiry_url(unpriced) == "https://x.invalid/vdp/1/car?mode=inquiry",
          inquiry_url(unpriced))
    check("appended to a listing that already has a query, not replacing it",
          inquiry_url(SimpleNamespace(price=None, listing_url="https://x.invalid/v?a=1"))
          == "https://x.invalid/v?a=1&mode=inquiry")
    check("and nothing invented for a car with no listing at all",
          inquiry_url(SimpleNamespace(price=None, listing_url="")) == "")

    def _payload(price, location, home):
        return _vehicle_payload(SimpleNamespace(
            vin="X", year=2020, make="BMW", model="X5", trim="", price=price,
            mileage=1000, body_style="suv", seats=5, features_json="[]",
            photo_url="", listing_url="https://x.invalid/vdp/1/car", status="available",
            raw_json=json.dumps({"location": location}) if location else None,
            rule_hold_price=False, rule_mention_warranty=False, rule_note="",
        ), home)

    # Its own key. `rule_hold_price` already owns `price_note`, and two notes
    # writing to one field means whichever runs last silently wins -- the one
    # that loses being a rule the dealer set.
    note = _payload(None, "", "")
    check("the model is told not to quote a price that does not exist",
          "do not quote or estimate" in note["no_price_note"]
          and "price_note" not in note)
    check("and told to point at the link rather than read a URL out",
          "never say a URL on a call" in note["no_price_note"])
    check("a priced car carries neither", "no_price_note" not in _payload(1, "", "")
          and "inquiry_url" not in _payload(1, "", ""))

    # Their lot is 240 cars in Louisville and 246 between two other stores, and
    # the appointment Liner books is at the one address in `dealerships`. A note
    # on every row would have it announce the store it is standing in on every
    # reply, which is noise -- and noise is how the one row that mattered stops
    # being read.
    home = "4156 shelbyville rd., louisville, ky 40207"
    here = _payload(9000, "Louisville", home)
    there = _payload(9000, "Clarksville", home)
    check("a car on this lot says where it is without making a point of it",
          here["location"] == "Louisville" and "location_note" not in here)
    check("and a car at another store is flagged before a time is offered",
          "Clarksville store" in there.get("location_note", ""),
          there.get("location_note", "(none)"))

    # Both bugs below were invisible on fourteen hand-written cars and obvious
    # on 486 real ones.
    check("a search word matches a whole word, not a fragment of one",
          not _hits("do", _words("Dodge Challenger Blu Bayou"))
          and not _hits("you", _words("Dodge Challenger Blu Bayou")),
          "'do' in 'Dodge' ranked a Hornet above every Corvette")
    check("and a plural finds the singular a listing is written in",
          _hits("corvettes", ["chevrolet", "corvette"])
          and _hits("trucks", ["truck"]))
    check("but not by chopping a letter off a short word",
          not _hits("gts", ["gt"]) and _hits("gt", ["gt"]))
    check("nobody types Chevrolet, and the lot is 74 of them",
          _hits("chevy", ["chevrolet", "trax"]) and _hits("vw", ["volkswagen", "jetta"]))
    # "a" is a whole word and it matches every A-Class and A-Spec on the lot,
    # so "do you have a chevy Trax?" ranked two cars nobody asked about above
    # the one that was named. No car is called "a"; several are called 3 and 5.
    check("a lone letter is filler and drops out of the search",
          _words("do you have a chevy trax?") == ["do", "you", "have", "chevy", "trax"],
          str(_words("do you have a chevy trax?")))
    check("but a lone digit is a model name and stays",
          _words("bmw 3 series") == ["bmw", "3", "series"],
          str(_words("bmw 3 series")))

    print("\n== what a crawl found, kept on disk ==")
    # The database is the product's answer to "what is on the lot"; this is
    # the crawl's answer to "what did the site say, when". An IngestRun keeps
    # a diff -- what *changed* -- so a field the adapter never read leaves no
    # trace at all once a run is published.
    from app.ingest import snapshot as snap
    from app.ingest.extract import Listing as _Listing

    was_profile = cfg.dealership
    try:
        cfg.dealership = f"smoke-{stamp}"
        cars = DealerCarSearch().parse_list(dcs_html, "https://example.invalid/newandusedcars")
        written = snap.write(cars, source_url="https://example.invalid/newandusedcars",
                             method="dealercarsearch", errors=[{"url": "x", "error": "y"}],
                             dealership_name="Smoke Motors")
        check("a crawl writes a snapshot into the dealership's own folder",
              written.is_file() and f"smoke-{stamp}" in str(written), str(written)[-60:])

        kept = json.loads(written.read_text())
        check("with the run's own provenance on it, not just the rows",
              kept["source_url"].endswith("newandusedcars")
              and kept["method"] == "dealercarsearch" and kept["captured_at"],
              f"{kept['method']} @ {kept['captured_at']}")
        check("and every field the adapter read, per car",
              kept["count"] == 3
              and {"vin", "year", "make", "model", "trim", "price", "mileage",
                   "photo_url", "listing_url", "features"} <= set(kept["vehicles"][0]),
              str(sorted(kept["vehicles"][0])[:6]))
        # The rows an adapter could not read are the most useful thing in the
        # file, so they are kept beside the ones it could.
        check("including what it could not read", kept["errors"] == [{"url": "x", "error": "y"}])

        # Two prospects' cars in one folder is one prospect's inventory
        # appearing in the other's demo.
        cfg.dealership = f"other-{stamp}"
        check("a second dealership gets a second folder",
              snap.read("Smoke Motors") is None, "it should be empty")
        cfg.dealership = f"smoke-{stamp}"
        check("and the first one still has its own", (snap.read("Smoke Motors") or {}).get("count") == 3)

        # Photos: never fatal, and never written from something that is not
        # an image -- the bytes come from somebody else's server.
        not_an_image = _Listing(vin="1FTFW1RG2SFC60236",
                                photo_url="http://127.0.0.1:1/nothing.jpg")
        got = snap.fetch_photos([not_an_image], dealership_name="Smoke Motors")
        check("a photo that will not download is reported, not raised",
              got["saved"] == 0 and len(got["failed"]) == 1, str(got["failed"])[:70])
        check("and nothing is written for it",
              snap.photo_path("1FTFW1RG2SFC60236", "Smoke Motors") is None)
    finally:
        cfg.dealership = was_profile
        for folder in (f"smoke-{stamp}", f"other-{stamp}"):
            path = cfg.inventory_dir / folder
            if path.is_dir():
                shutil.rmtree(path)

    # Where a car's picture comes from, in order. The dealer's own URL is the
    # default and stays the default: their CDN is faster and closer than this
    # box, costs nothing, and stays current when they swap a photo -- a
    # downloaded copy quietly goes stale. A stored copy wins only where one
    # exists, which is only when somebody turned SCRAPER_SAVE_PHOTOS on.
    from app.ingest.pipeline import _photo_for

    check("photos are hotlinked from the dealer by default, not copied",
          _photo_for({"vin": "1FTFW1RG2SFC60236",
                      "photo_url": "https://imagescdn.example.invalid/a.jpg"})
          == "https://imagescdn.example.invalid/a.jpg")
    check("and downloading is off unless a deployment asks for it",
          cfg.__class__().scraper_save_photos is False)

    was = cfg.dealership
    try:
        cfg.dealership = f"photos-{stamp}"
        kept = snap.folder() / "photos"
        kept.mkdir(parents=True, exist_ok=True)
        (kept / "1FTFW1RG2SFC60236.jpg").write_bytes(b"\xff\xd8\xff")
        # If a copy is downloaded and the rows still point at the internet,
        # the setting downloaded 481 files for nothing.
        check("but a stored copy is actually the one served",
              _photo_for({"vin": "1FTFW1RG2SFC60236",
                          "photo_url": "https://imagescdn.example.invalid/a.jpg"})
              == "/api/photos/1FTFW1RG2SFC60236")
    finally:
        cfg.dealership = was
        stale = cfg.inventory_dir / f"photos-{stamp}"
        if stale.is_dir():
            shutil.rmtree(stale)

    # A car with no stored photo still renders: a lot imported from a CSV has
    # no photos at all and its rows have to draw something.
    drawn = status_of("GET", "/api/photos/NOSUCHVIN00000000")
    check("a vehicle with no photo on disk still gets a picture",
          drawn[0] == 200 and "<svg" in drawn[1], str(drawn[0]))

    # And looking for one does not create anything. `/api/photos/{vin}` runs
    # this on every request, so a lookup that made directories left an empty
    # folder behind for every placeholder ever drawn -- all with the same
    # fallback name, so the debris read as a real dealership's directory.
    probe = cfg.inventory_dir / "never-crawled-anything"
    was = cfg.dealership
    try:
        cfg.dealership = "never-crawled-anything"
        snap.photo_path("NOSUCHVIN00000000")
        snap.read()
        check("and looking one up creates no folder for a dealership with none",
              not probe.exists(), str(probe))
    finally:
        cfg.dealership = was
        if probe.is_dir():
            shutil.rmtree(probe)

    print("\n== the run gives back the slots it took ==")
    # Every booking above holds a time that book_appointment will refuse to
    # double-book. Without releasing them each run eats into the fixture's
    # week, and after enough runs check_availability has nothing to offer and
    # the booking flow fails -- which is exactly what happened. `make smoke`
    # must stay runnable against a database that has already seen it.
    release_slots()
    # What matters is that none of them is still holding a time, not how many
    # this call had left to cancel -- several sections cancel their own booking
    # as part of what they are testing, and counting cancellations here made
    # those look like failures.
    holding = [
        a["id"] for a in call("GET", "/api/appointments")["appointments"]
        if a["id"] in booked_here and a["status"] in ("booked", "confirmed")
    ]
    check("no slot this run took is still held", not holding,
          f"{len(holding)} of {len(booked_here)} still booked")
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
    try:
        sys.exit(main())
    finally:
        # Not tidiness -- this is what stops one bad run degrading every run
        # after it. A crash mid-script is exactly when the slots are most
        # likely to be stranded, and least likely to be noticed.
        try:
            stranded = release_slots()
            if stranded:
                print(f"\nReleased {stranded} appointment(s) held by this run.")
        except Exception as exc:  # the run is already over; say so and stop
            print(f"\nCould not release this run's appointments: {exc}")
