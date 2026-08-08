#!/usr/bin/env python3
"""Drive the entire flow over HTTP. No browser, no credentials, no network.

Buyer books an appointment by tapping rails, then the dealer side confirms,
assigns and sends outreach. This is the primary gate: it must pass against a
clean seed with an empty .env.

    make smoke
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
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
    call("POST", f"/api/leads/{lead_for_credit['id']}/outreach",
         {"subject": draft["subject"], "body": draft["body"], "kind": "credit_application"})
    after = next(k for k in call("GET", "/api/overview")["kpis"] if k["key"] == "credit_apps")["value"]
    check("sending one moves the card", after == before + 1, f"{before} -> {after}")

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
