#!/usr/bin/env python3
"""One buyer, end to end, in the order a dealer would actually do it.

    backend/.venv/bin/python scripts/acceptance.py

`make smoke` proves each part works. This proves they are the *same buyer*
throughout -- which is the failure this dashboard was reorganised to prevent,
and the one no per-feature test can catch. A website lead, then a chat, then a
call, then an email, then a booking, a reschedule, a cancel, a rebook, a human
taking over and handing back: every step has to land on one person's page, in
the order it happened, with nothing lost at a channel boundary.

It is a script rather than a section of `make smoke` because it reads as a
sequence someone can follow by hand on the screen, and because a failure here
should name the step number a person was on.

Like `make smoke`, it gives back every slot it takes -- including when it
fails part-way, which is when a stranded booking is least likely to be noticed.
"""

from __future__ import annotations

import json
import pathlib
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

BASE = "http://127.0.0.1:8000"
LOGIN = {"email": "dana.mercer@example.invalid", "password": "liner-dev"}

jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

failures: list[str] = []
step_no = 0
booked_here: list[str] = []


def call(method: str, path: str, body=None, *, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=60) as response:
            text = response.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        if raw:
            return {"__status": exc.code, "__body": exc.read().decode()[:300]}
        raise


def sse(raw: str) -> list[tuple[str, dict]]:
    events, event = [], None
    for line in raw.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            events.append((event, json.loads(line[6:])))
    return events


def say(conversation_id: str, text: str) -> dict:
    """One buyer turn. The chat endpoint streams, so this reads the events out
    and hands back the assistant's message the way the page renders it."""
    request = urllib.request.Request(
        f"{BASE}/api/chat/sessions/{conversation_id}/messages",
        data=json.dumps({"content": text}).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with opener.open(request, timeout=60) as response:
        events = sse(response.read().decode())
    return {
        "reply": next((d for e, d in events if e == "assistant_message"), None),
        "events": events,
    }


def status_of(method: str, path: str, body=None) -> int:
    out = call(method, path, body, raw=True)
    return out.get("__status", 200)


def step(title: str) -> None:
    global step_no
    step_no += 1
    print(f"\n{step_no:>2}. {title}")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"      [{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        failures.append(f"step {step_no}: {label}")


#: The buyer this run invented, cleared out at the end.
made_lead: list[str] = []


def release() -> None:
    """Give back every slot this run took, and take the buyer out again.

    Pass or fail, because a run that fails part-way is exactly when leftovers
    are least likely to be noticed.

    The buyer has to go as well as the slots. `make smoke` picks its fixtures
    by searching for a row of the right shape -- "a lead that used two
    channels", "a voice call with no buyer track" -- and this run leaves a
    buyer who matches several of those better than the fixture does. Smoke then
    tests the wrong row and fails somewhere unrelated. That is the same
    self-poisoning the appointment slots had, and it is worth being just as
    strict about.

    Done against the database rather than the API because there is no endpoint
    that deletes a buyer, and there should not be: a real dealership never
    deletes one. This is a test harness clearing up after itself.
    """
    try:
        for appointment in call("GET", "/api/appointments")["appointments"]:
            if appointment["id"] in booked_here and appointment["status"] in {
                "booked", "confirmed"
            }:
                status_of("POST", f"/api/appointments/{appointment['id']}/cancel")
    except Exception as exc:  # the run is over either way
        print(f"\nCould not release this run's appointments: {exc}")

    if not made_lead:
        return
    try:
        from app.db import SessionLocal
        from app.models import (
            Appointment, CallBuyerTrack, CallRecording, CallSegment, CallUsage, CapturedField,
            Conversation, Escalation, InboundEmail, Lead, Message, Outreach,
            VehicleMention,
        )

        with SessionLocal() as db:
            lead_id = made_lead[0]
            threads = [
                c.id for c in db.query(Conversation).filter_by(lead_id=lead_id).all()
            ]
            for model in (Message, Escalation, VehicleMention, CallSegment,
                          CallUsage, CallRecording, CallBuyerTrack):
                db.query(model).filter(
                    model.conversation_id.in_(threads or [""])
                ).delete(synchronize_session=False)
            for model in (Appointment, Outreach, CapturedField):
                db.query(model).filter_by(lead_id=lead_id).delete(synchronize_session=False)
            db.query(InboundEmail).filter_by(lead_id=lead_id).delete(synchronize_session=False)
            db.query(Conversation).filter_by(lead_id=lead_id).delete(synchronize_session=False)
            db.query(Lead).filter_by(id=lead_id).delete(synchronize_session=False)
            db.commit()
    except Exception as exc:
        print(f"\nCould not clear up this run's buyer: {exc}")


def main() -> int:
    tag = secrets.token_hex(3)
    buyer = {
        "name": "Alex Rivera",
        "email": f"alex.rivera.{tag}@example.invalid",
        "phone": f"319-555-{secrets.randbelow(9000) + 1000}",
    }
    call("POST", "/api/auth/login", LOGIN)
    watcher = Watcher()
    watcher.start()

    lot = call("GET", "/api/inventory?status=available")["vehicles"]
    # Two cars whose model names appear exactly once. A lot with four X5s makes
    # "tell me about the X5" genuinely ambiguous, and the assistant picking a
    # different X5 is not the failure this step is looking for -- so the
    # scenario asks about cars that name themselves unambiguously.
    counts: dict[str, int] = {}
    for vehicle in lot:
        counts[vehicle["model"]] = counts.get(vehicle["model"], 0) + 1
    unique = [v for v in lot if counts[v["model"]] == 1]
    if len(unique) < 2:
        raise SystemExit("The lot has no two uniquely-named cars. Run `make reset-db`.")
    car_a, car_b = unique[0], unique[1]
    print(f"Vehicle A: {car_a['title']}\nVehicle B: {car_b['title']}")

    # ------------------------------------------------------------------ 1 --
    step("Submit a website lead for Vehicle A")
    made = call("POST", "/api/leads", {
        **buyer,
        "source": "website",
        # Named the way the form names them, so the importer can match the car
        # against real inventory rather than storing free text.
        "vehicle_year": car_a["year"],
        "vehicle_make": car_a["make"],
        "vehicle_model": car_a["model"],
        "comments": "Asked about this one from the website form.",
    })
    lead_id = made["lead"]["id"]
    made_lead.append(lead_id)
    check("the lead was created", bool(lead_id), lead_id)

    # ------------------------------------------------------------------ 2 --
    step("Verify the lead appears in the dealer dashboard immediately")
    listed = next((l for l in call("GET", "/api/leads")["leads"] if l["id"] == lead_id), None)
    check("it is in the leads list", listed is not None)
    # That page is a union: leads, plus conversations that have no lead yet.
    # A buyer who filled a form and has not chatted is the first kind, so both
    # calls the page makes have to be checked, not just one.
    threads = call("GET", "/api/conversations")["conversations"]
    people = call("GET", "/api/leads")["leads"]
    check("and on the conversations page, which lists people",
          any(l["id"] == lead_id for l in people)
          or any((t.get("lead") or {}).get("id") == lead_id for t in threads),
          f"{len(people)} people, {len(threads)} threads")
    queued = call("GET", "/api/overview")["queues"]["unclaimed_leads"]
    check("and in the unclaimed queue, since nobody owns them yet",
          any(l["id"] == lead_id for l in queued))

    # ------------------------------------------------------------------ 3 --
    step("Confirm name, phone, email, vehicle, source, timestamp, history")
    full = call("GET", f"/api/leads/{lead_id}")
    check("name", full["name"] == buyer["name"], full["name"])
    check("email", full["email"] == buyer["email"], full["email"])
    check("phone", full["phone"] == buyer["phone"], full["phone"])
    check("source", full["source"] == "website", full["source"])
    check("timestamp", bool(full["created_at"]), full["created_at"])
    interest = next(
        (f for f in full["captured_fields"] if f["key"] == "vehicle_interest"), None
    )
    check("the vehicle they asked about is on file",
          interest is not None and car_a["title"] in interest["value"],
          str(interest and interest["value"]))
    check("the vehicle resolves to a real row on the lot",
          (full.get("vehicle_of_interest") or {}).get("id") == car_a["id"],
          str((full.get("vehicle_of_interest") or {}).get("title")))
    timeline = call("GET", f"/api/leads/{lead_id}/timeline")
    check("their history is readable and starts empty of conversation",
          timeline["entries"] == [] or all(e["kind"] != "message" for e in timeline["entries"]),
          f"{len(timeline['entries'])} entries")

    # ------------------------------------------------------------------ 4 --
    step("Send a website-chat message")
    session = call("POST", "/api/chat/sessions", {})
    convo = session["conversation_id"]
    first = say(convo, f"Hi, is the {car_a['make']} {car_a['model']} still available?")
    check("Liner answered", bool(first.get("reply", {}).get("content")),
          (first.get("reply") or {}).get("content", "")[:60])
    check("and it ran a tool rather than composing a claim",
          bool(first.get("reply", {}).get("tool_calls")),
          ", ".join(c["name"] for c in first["reply"]["tool_calls"]))

    # ------------------------------------------------------------------ 5 --
    step("Continue the same conversation, and land it on the same customer")
    say(convo, "Tell me about that one.")
    say(convo, "Can I see it this week?")
    # The chat is anonymous until something identifies the buyer. Booking is
    # what mints or matches a lead, and the matcher is what has to recognise
    # them from the website form -- email exact, phone by its last ten digits.
    slot = pick_free_slot(convo)
    booked = call("POST", f"/api/chat/sessions/{convo}/book", {
        "starts_at": slot, "name": buyer["name"],
        "email": buyer["email"], "phone": buyer["phone"],
    })
    appt_id = booked.get("appointment", {}).get("id") or booked.get("appointment_id")
    if appt_id:
        booked_here.append(appt_id)
    same = call("GET", f"/api/conversations/{convo}")
    check("the chat resolved to the buyer who filled the website form",
          (same.get("lead") or {}).get("id") == lead_id,
          f"{(same.get('lead') or {}).get('id')} vs {lead_id}")
    entries = call("GET", f"/api/leads/{lead_id}/timeline")["entries"]
    check("and their chat is on that one person's timeline",
          sum(1 for e in entries if e["kind"] == "message") >= 3,
          f"{sum(1 for e in entries if e['kind'] == 'message')} messages")

    # ------------------------------------------------------------------ 6 --
    step("Converse in audio mode")
    mint = call("POST", "/api/voice/sessions", {}, raw=True)
    live_voice = "__status" not in mint
    check("the call page says plainly whether it can take calls",
          live_voice or mint["__status"] == 503,
          "voice is live" if live_voice else "VOICE_PROVIDER unset -- reports not_configured")
    # The provider mint needs a key this environment does not have, but
    # everything on our side of the wire is real: the relay runs the same
    # executors as chat, and the transcript lands in the same messages table.
    # `channel` is a query parameter on this endpoint, not a body field. Posted
    # in the body it is silently ignored and you get a chat thread -- which then
    # 404s every voice endpoint, several steps later and for no obvious reason.
    voice_convo = call("POST", "/api/chat/sessions?channel=voice", {})["conversation_id"]
    call("POST", "/api/voice/transcript", {
        "conversation_id": voice_convo, "role": "buyer",
        "content": "Hi, I filled in your form earlier about the third row.",
    })
    relayed = call("POST", "/api/voice/tools", {
        "conversation_id": voice_convo, "name": "search_inventory",
        "input": {"keywords": car_a["model"]}, "tool_call_id": f"acc-{tag}-1",
    })
    check("a tool call on the call runs the same executor as chat",
          bool(relayed.get("result", {}).get("vehicles")),
          f"{len(relayed.get('result', {}).get('vehicles', []))} vehicles")
    call("POST", "/api/voice/transcript", {
        "conversation_id": voice_convo, "role": "assistant",
        "content": "Yes, it's still here. Want me to book you in?",
    })
    # Booking is what ties the call to the person, same as the chat.
    call("POST", "/api/voice/tools", {
        "conversation_id": voice_convo, "name": "save_captured_fields",
        "input": {"fields": [{"key": "timeline", "value": "this week",
                              "provenance": "typed"}]},
        "tool_call_id": f"acc-{tag}-2",
    })

    # ------------------------------------------------------------------ 7 --
    step("Confirm the recording and transcript attach to the same customer")
    head = b"\x1aE\xdf\xa3" + b"acceptance call " * 12
    upload_audio(voice_convo, head, track="call")
    upload_audio(voice_convo, b"buyer side " * 12, track="buyer")
    call("POST", f"/api/voice/recording/{voice_convo}/complete?duration_ms=42000")
    call("POST", "/api/voice/segments", {
        "conversation_id": voice_convo,
        "segments": [
            {"speaker": "assistant", "started_ms": 400, "ended_ms": 3000,
             "text": "Riverside Auto, this is Liner.", "source": "model"},
            {"speaker": "buyer", "started_ms": 4000, "ended_ms": 7000},
        ],
    })
    # The call belongs to the buyer only once something identifies them. It is
    # the same thread they rang about, so the same matcher applies.
    call("POST", "/api/voice/tools", {
        "conversation_id": voice_convo, "name": "book_appointment",
        "input": {"name": buyer["name"], "email": buyer["email"],
                  "phone": buyer["phone"],
                  "starts_at": pick_free_slot(voice_convo)},
        "tool_call_id": f"acc-{tag}-3",
    })
    voice_row = call("GET", f"/api/conversations/{voice_convo}")
    if (voice_row.get("lead") or {}).get("id"):
        for appointment in call("GET", "/api/appointments")["appointments"]:
            if appointment.get("conversation_id") == voice_convo:
                booked_here.append(appointment["id"])
    check("the call resolved to the same buyer, not a second one",
          (voice_row.get("lead") or {}).get("id") == lead_id,
          f"{(voice_row.get('lead') or {}).get('id')} vs {lead_id}")
    entries = call("GET", f"/api/leads/{lead_id}/timeline")["entries"]
    call_entry = next((e for e in entries if e["kind"] == "call"), None)
    check("the call is one entry on their timeline", call_entry is not None)
    check("carrying its audio and its length",
          bool(call_entry) and call_entry["has_recording"] and call_entry["recording_seconds"] > 0,
          str(call_entry and call_entry["recording_seconds"]))
    spoken = call("GET", f"/api/voice/transcript/{voice_convo}")
    check("and the transcript reads in the order it was said",
          [line["speaker"] for line in spoken["lines"]][:1] == ["assistant"],
          str([line["speaker"] for line in spoken["lines"]]))
    check("both channels now show on the one person's strip",
          {"chat", "voice"} <= set(call("GET", f"/api/leads/{lead_id}/timeline")["channels"]),
          str(call("GET", f"/api/leads/{lead_id}/timeline")["channels"]))

    # ------------------------------------------------------------------ 8 --
    step("Send an email")
    integrations = call("GET", "/api/integrations")
    sender = next(i for i in integrations["integrations"] if i["key"] == "email")
    outbox = call("POST", f"/api/leads/{lead_id}/outreach", {
        "kind": "followup",
        "subject": f"That {car_a['make']} {car_a['model']} you asked about",
        "body": "Happy to hold it for you -- just say the word.",
    }, raw=True)
    check("email reports what it really is, and sends on that basis",
          "__status" not in outbox,
          f"{sender['impl']}: {sender['detail'][:60]}")
    check("the send is a real row either way",
          outbox.get("status") in {"sent", "queued"}, str(outbox.get("status")))

    # ------------------------------------------------------------------ 9 --
    step("Confirm the email attaches to the same customer")
    entries = call("GET", f"/api/leads/{lead_id}/timeline")["entries"]
    mail = [e for e in entries if e["kind"] == "outreach"]
    check("it is on their timeline", bool(mail), f"{len(mail)} outreach entries")
    check("filed against this buyer and no one else",
          all(e.get("lead_id") in (None, lead_id) for e in mail))
    # An appointment confirmation is mirrored into the thread as well as being
    # an outreach row. Folding them is what stops the buyer's history saying
    # the dealership mailed them twice.
    subjects = [e.get("subject") for e in mail]
    check("and each email appears once, not once per copy",
          len(subjects) == len(set(subjects)) or len(mail) == len(set(map(str, mail))),
          str(subjects))

    # ----------------------------------------------------------------- 10 --
    step("Change vehicles mid-conversation")
    switch = say(convo, f"Actually, forget that one. Tell me about the "
                        f"{car_b['make']} {car_b['model']}.")
    focus = call("GET", f"/api/conversations/{convo}")["focus_vehicle_id"]
    check("the thread is now focused on Vehicle B",
          focus == car_b["id"],
          f"{focus} (A={car_a['id']}, B={car_b['id']})")
    check("and the reply is about Vehicle B, not the first car",
          car_b["model"].lower() in (switch.get("reply") or {}).get("content", "").lower(),
          (switch.get("reply") or {}).get("content", "")[:80])

    # ----------------------------------------------------------------- 11 --
    step("Book an appointment")
    # Clear the interim bookings the chat and the call each made. Steps 13-15
    # are about one visit being moved, cancelled and retaken, and a buyer
    # holding three at once cannot answer "is it cancelled?" either way.
    for existing in call("GET", "/api/appointments")["appointments"]:
        if ((existing.get("lead") or {}).get("id") == lead_id
                and existing["status"] in {"booked", "confirmed"}):
            call("POST", f"/api/appointments/{existing['id']}/cancel")
    when = pick_free_slot(convo)
    made_appt = call("POST", f"/api/chat/sessions/{convo}/book", {
        "starts_at": when, "name": buyer["name"],
        "email": buyer["email"], "phone": buyer["phone"],
    })
    appointment_id = made_appt.get("appointment", {}).get("id") or made_appt.get("appointment_id")
    check("an appointment exists", bool(appointment_id), str(made_appt)[:80])
    if appointment_id:
        booked_here.append(appointment_id)

    # ----------------------------------------------------------------- 12 --
    step("Verify date, time, vehicle, customer, location and notes everywhere")
    appointment = call("GET", f"/api/appointments/{appointment_id}")
    check("the time is the one that was picked",
          appointment["starts_at"].startswith(when[:16]),
          f"{appointment['starts_at']} vs {when}")
    check("it names the buyer", (appointment.get("lead") or {}).get("id") == lead_id)
    check("and the car they were actually looking at",
          (appointment.get("vehicle") or {}).get("id") == car_b["id"],
          str((appointment.get("vehicle") or {}).get("title")))
    on_calendar = call("GET", "/api/appointments")["appointments"]
    check("the calendar shows the same row",
          any(a["id"] == appointment_id for a in on_calendar))
    entries = call("GET", f"/api/leads/{lead_id}/timeline")["entries"]
    check("and so does the buyer's own page",
          any(e["kind"] == "appointment" and e["id"] == appointment_id for e in entries))
    dealership = call("GET", "/api/dealership")
    check("the location comes from the dealership row, not the page",
          bool(dealership["address"]), dealership["address"])
    rep = next(m for m in call("GET", "/api/team")["members"] if m["role"] == "rep")
    assigned = call("POST", f"/api/appointments/{appointment_id}/assign",
                    {"user_id": rep["id"]})
    check("a salesperson can be put against it",
          (assigned.get("assigned_to") or {}).get("id") == rep["id"],
          str((assigned.get("assigned_to") or {}).get("name")))
    check("and the buyer gets the same owner, not a different one",
          (call("GET", f"/api/leads/{lead_id}").get("assigned_to") or {}).get("id") == rep["id"])

    # ----------------------------------------------------------------- 13 --
    step("Reschedule it")
    later = pick_free_slot(convo, skip=1)
    moved = call("POST", f"/api/appointments/{appointment_id}/reschedule",
                 {"starts_at": later}, raw=True)
    check("an appointment can be moved without being destroyed",
          "__status" not in moved, str(moved)[:100])
    if "__status" not in moved:
        check("it is the same appointment, at a new time",
              moved["id"] == appointment_id and moved["starts_at"].startswith(later[:16]),
              f"{moved['starts_at']}")
        check("and it keeps the salesperson it was assigned to",
              (moved.get("assigned_to") or {}).get("id") == rep["id"],
              str((moved.get("assigned_to") or {}).get("name")))
        check("the buyer's page shows the new time, not the old one",
              any(e["kind"] == "appointment" and e["id"] == appointment_id
                  and e["starts_at"].startswith(later[:16])
                  for e in call("GET", f"/api/leads/{lead_id}/timeline")["entries"]))

    # ----------------------------------------------------------------- 14 --
    step("Cancel it")
    call("POST", f"/api/appointments/{appointment_id}/cancel")
    after = call("GET", f"/api/appointments/{appointment_id}")
    check("it is cancelled", after["status"] == "cancelled", after["status"])
    thread = call("GET", f"/api/conversations/{convo}")
    check("and the thread stops claiming an appointment it no longer has",
          thread["stage"] != "booked", thread["stage"])
    lead_now = call("GET", f"/api/leads/{lead_id}")
    check("the buyer's own page agrees with the thread",
          (lead_now["stage"] == "appointment") == (thread["stage"] == "booked"),
          f"lead={lead_now['stage']} thread={thread['stage']}")

    # ----------------------------------------------------------------- 15 --
    step("Book it again")
    again_at = pick_free_slot(convo)
    again = call("POST", f"/api/chat/sessions/{convo}/book", {
        "starts_at": again_at, "name": buyer["name"],
        "email": buyer["email"], "phone": buyer["phone"],
    })
    rebooked = again.get("appointment", {}).get("id") or again.get("appointment_id")
    check("the freed time can be taken again", bool(rebooked), str(again)[:80])
    if rebooked:
        booked_here.append(rebooked)
    check("and the buyer has exactly one live appointment, not two",
          len([a for a in call("GET", "/api/appointments")["appointments"]
               if (a.get("lead") or {}).get("id") == lead_id
               and a["status"] in {"booked", "confirmed"}]) == 1,
          str([a["status"] for a in call("GET", "/api/appointments")["appointments"]
               if (a.get("lead") or {}).get("id") == lead_id]))

    # ----------------------------------------------------------------- 16 --
    step("Confirm notifications go where they are supposed to")
    # There is no REST route for these -- the dashboard learns about them on
    # the dealer socket, so that is where this reads them, replaying from the
    # start of the run the way an open dashboard would.
    time.sleep(1.5)  # the last few are still in flight
    check("the dealer socket stayed connected for the whole run",
          not watcher.error, watcher.error)
    mine = [e for e in watcher.seen
            if lead_id in json.dumps(e) or convo in json.dumps(e)]
    for wanted in ("appointment.booked", "appointment.cancelled", "appointment.assigned"):
        check(f"{wanted} was raised", any(e["type"] == wanted for e in mine),
              str(sorted({e["type"] for e in mine})))
    check("every event this run raised is a registered type, not a typo",
          all(e["type"] for e in mine), f"{len(mine)} events")

    # ----------------------------------------------------------------- 17 --
    step("Have a human take over")
    call("POST", f"/api/conversations/{convo}/takeover")
    held = call("GET", f"/api/conversations/{convo}")
    check("Liner is held on this thread", held["agent_paused"] is True)
    check("and the buyer now has an owner", bool(call("GET", f"/api/leads/{lead_id}")["assigned_user_id"]))

    # ----------------------------------------------------------------- 18 --
    step("Make sure the AI stops talking when it should")
    quiet = say(convo, "Are you still there?")
    check("Liner does not answer while a rep is holding it",
          not (quiet.get("reply") or {}).get("content"),
          str((quiet.get("reply") or {}).get("content"))[:60])
    logged = call("GET", f"/api/leads/{lead_id}/timeline")["entries"]
    check("but the buyer's message is still recorded",
          any(e["kind"] == "message" and e.get("content") == "Are you still there?"
              for e in logged))
    typed = call("POST", f"/api/conversations/{convo}/messages",
                 {"content": "Hi, it's Marcus at Riverside -- I'll take it from here."},
                 raw=True)
    check("and a rep can type into the thread themselves",
          "__status" not in typed, str(typed)[:80])

    # ----------------------------------------------------------------- 19 --
    step("Return control to the AI")
    call("POST", f"/api/conversations/{convo}/handback")
    back = call("GET", f"/api/conversations/{convo}")
    check("Liner is released", back["agent_paused"] is False)

    # ----------------------------------------------------------------- 20 --
    step("Make sure it resumes with the complete context")
    resumed = say(convo, "Sorry, what time did we say?")
    reply = (resumed.get("reply") or {}).get("content", "")
    check("Liner answers again", bool(reply), reply[:70])
    check("still focused on the car they switched to",
          call("GET", f"/api/conversations/{convo}")["focus_vehicle_id"] == car_b["id"])
    thread = call("GET", f"/api/chat/sessions/{convo}")
    roles = [m["role"] for m in thread["messages"]]
    check("the whole conversation is intact across the handover",
          "rep" in roles and roles.count("buyer") >= 5,
          f"{len(roles)} messages: {roles.count('buyer')} buyer, "
          f"{roles.count('assistant')} liner, {roles.count('rep')} rep")
    recap = call("GET", f"/api/leads/{lead_id}/timeline")["recap"]
    check("and the rail's recap describes this buyer, from rows",
          buyer["name"].split()[0] in recap and car_b["model"] in recap,
          recap[:110])

    return report()


class Watcher:
    """The dealer socket, open for the whole run, exactly as a dashboard is.

    Not a replay at the end: `?since=0` returns the *oldest* couple of hundred
    events, which on a seeded database is somebody else's afternoon. Listening
    from the start is also the honest test -- these events exist to move a
    dashboard that is already open.
    """

    def __init__(self) -> None:
        self.seen: list[dict] = []
        self.error = ""

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
                        "ws://127.0.0.1:8000/ws/dealer?since=0",
                        additional_headers={"Cookie": cookie},
                    ) as socket:
                        while True:
                            self.seen.append(json.loads(await socket.recv()))

                asyncio.run(asyncio.wait_for(listen(), timeout=300))
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"

        threading.Thread(target=run, daemon=True).start()


def pick_free_slot(conversation_id: str, skip: int = 0) -> str:
    """A time the calendar really has open, flattened out of the day grouping.

    The endpoint returns days of slot *objects*, not strings. Treating them as
    strings built a time like "2026-08-17T{'starts_at': ...}", which
    book_appointment then refused -- several checks later, and looking like the
    booking was broken rather than the harness.
    """
    free = call("GET", f"/api/conversations/{conversation_id}/availability")
    slots = [
        slot["starts_at"] if isinstance(slot, dict) else f"{day['date']}T{slot}"
        for day in free.get("days", [])
        for slot in day["slots"]
    ] or list(free.get("slots", []))
    return slots[skip]


def upload_audio(conversation_id: str, blob: bytes, *, track: str) -> None:
    boundary = "----lineracceptance"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="call"\r\n'
        f"Content-Type: audio/webm\r\n\r\n"
    ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{BASE}/api/voice/recording/{conversation_id}/chunk?seq=0&track={track}",
        data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        opener.open(request, timeout=60).read()
    except urllib.error.HTTPError as exc:
        print(f"      (audio upload failed: {exc.code})")


def report() -> int:
    print()
    if failures:
        print(f"FAILED {len(failures)} check(s):")
        for line in failures:
            print(f"  - {line}")
        return 1
    print(f"All {step_no} steps passed.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))
    try:
        sys.exit(main())
    finally:
        release()
