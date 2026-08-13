#!/usr/bin/env python3
"""Drive the live agent loop against a fake provider. No API key, no network.

The live loop is the one path that cannot be tested the usual way -- it needs a
paid endpoint. Leaving it untested is how the guards quietly stop running on
the path that most needs them, so instead the loop takes an injectable provider
and this drives it with a scripted one.

Real here: tool dispatch, the executors, the rows they write, the tool-error
path, the guard verdict, the corrective retry, the escalation, and the exact
message list a vendor would receive. Absent: the HTTP call itself.

    backend/.venv/bin/python scripts/agent_loop_check.py

Run by `make smoke`. It needs a seeded database and nothing else.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from app.agent import loop  # noqa: E402
from app.agent.fake_provider import FakeProvider, call_tool, say  # noqa: E402
from app.agent import tools  # noqa: E402
from app.agent.providers import Completion, ToolCall, _openai_tools  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Conversation, Escalation, Vehicle  # noqa: E402
from app.schemas.serialize import conversation_out  # noqa: E402


def kind(item) -> str:
    """A transcript entry's type, whatever shape it is. The list is not
    homogeneous: a reasoning model's own turn goes back as opaque objects."""
    return item.get("type", "") if isinstance(item, dict) else getattr(item, "type", "")

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def fresh_conversation(db) -> Conversation:
    convo = Conversation(channel="chat", stage="opening")
    db.add(convo)
    db.commit()
    return convo


def main() -> int:
    db = SessionLocal()
    try:
        print("\n== tool schema conversion ==")
        schema = _openai_tools()
        from app.agent import tools as tool_mod

        check("every tool is offered to the model", len(schema) == len(tool_mod.TOOL_DEFS),
              f"{len(schema)} tools")
        check("each is a function with a name and parameters",
              all(t["type"] == "function" and t["name"]
                  and "properties" in t["parameters"] for t in schema))
        check("search_inventory survived the conversion",
              any(t["name"] == "search_inventory" for t in schema))

        print("\n== a turn that calls a tool, then answers ==")
        convo = fresh_conversation(db)
        # Two passes: the first learns what is actually on the lot, the second
        # quotes it back. Hardcoding a price made this a test of the fixture --
        # seed a different lot and the guards correctly reject a car that is not
        # there, which looked like a regression.
        probe = FakeProvider([call_tool("search_inventory", keywords="third row",
                                        max_price=25000), say("...")])
        _, probe_calls = loop.run_turn(db, convo, "Something with a third row under 25k",
                                       provider=probe)
        top = (probe_calls[0]["result"].get("vehicles") or [{}])[0]
        quote = f"{top.get('year')} {top.get('make')} {top.get('model')} at ${top.get('price'):,}"

        convo = fresh_conversation(db)
        provider = FakeProvider([
            call_tool("search_inventory", keywords="third row", max_price=25000),
            say(f"We have a {quote}. Want to come and see it?"),
        ])
        reply, calls = loop.run_turn(db, convo, "Something with a third row under 25k",
                                     provider=provider)
        check("the tool actually ran", any(c["name"] == "search_inventory" for c in calls))
        found = calls[0]["result"].get("vehicles", []) if calls else []
        check("it returned real inventory", bool(found), f"{len(found)} vehicles")
        # Asserting on specific models tested the fixture, not the search: it
        # broke the moment a bigger lot was seeded. The property that matters is
        # that every hit actually claims what was asked for, and that the cap
        # held -- true whatever is on the lot.
        # Not a keyword match: `keywords` is an internal column and is
        # deliberately absent from the payload, so asserting on it passed only
        # by accident. Seats is the honest test of "has a third row", and it is
        # a fact about the car rather than about how it was indexed.
        check("ranked on the need, not just the price cap",
              all((v.get("seats") or 0) >= 7
                  or "third row" in " ".join(v.get("features", [])).lower()
                  for v in found),
              ", ".join(f"{v.get('model', '?')}/{v.get('seats')}" for v in found))
        check("the price cap held",
              all((v.get("price") or 0) <= 25000 for v in found))

        print("\n== the input list the Responses API would receive ==")
        sent = provider.seen_messages[-1]
        made = [m for m in sent if kind(m) == "function_call"]
        answered = [m for m in sent if kind(m) == "function_call_output"]
        check("the model's own function_call is echoed back", len(made) == 1)
        check("with one output per call_id -- omitting one is a 400",
              len(answered) == len(made), f"{len(answered)} outputs")
        check("and the call_ids line up",
              {m["call_id"] for m in answered} == {m["call_id"] for m in made})
        check("tool schemas are flat, as Responses wants them",
              all("name" in t and "function" not in t for t in schema))
        check("the system prompt was sent as instructions",
              bool(provider.seen_systems[-1].strip()))
        # Every request, not just the last: an empty input is a 400 whose
        # message names the SDK rather than the empty conversation.
        check("no request was sent with an empty input",
              all(len(m) > 0 for m in provider.seen_messages),
              f"{len(provider.seen_messages)} requests")
        check("the buyer's own message is in the first request",
              any(isinstance(m, dict) and m.get("role") == "user"
                  for m in provider.seen_messages[0]))
        # The turn above survived a transcript containing a reasoning item.
        # Treating every entry as a dict crashed *after* a successful 200 from
        # the API, which reads as a broken integration rather than a bug here.
        check("a reasoning item in the transcript does not break the turn",
              any(kind(m) == "reasoning" for m in sent),
              f"{sum(1 for m in sent if kind(m) == 'reasoning')} reasoning items survived")

        print("\n== a do-not-discuss vehicle never reaches the model ==")
        blocked = db.query(Vehicle).filter_by(rule_discuss=False).first()
        restore = False
        if blocked is None:
            # Borrowed, not taken. This used to flip a real vehicle and leave it
            # flipped, so every `make smoke` quietly removed one more car from
            # the lot -- on a live install too.
            blocked = db.query(Vehicle).first()
            blocked.rule_discuss = False
            db.commit()
            restore = True
        convo = fresh_conversation(db)
        _, calls = loop.run_turn(db, convo, "anything", provider=FakeProvider([
            call_tool("search_inventory", keywords=f"{blocked.make} {blocked.model}"),
            say("Here is what we have."),
        ]))
        returned = [v for c in calls for v in c["result"].get("vehicles", [])]
        # The search has to have worked, or "the VIN is absent" would pass for
        # the wrong reason -- an errored tool returns no vehicles at all.
        check("the search ran and returned something", bool(returned),
              f"{len(returned)} vehicles")
        check("but not the do-not-discuss one",
              blocked.vin not in [v["vin"] for v in returned],
              f"{blocked.year} {blocked.make} {blocked.model} withheld")
        if restore:
            blocked.rule_discuss = True
            db.commit()

        print("\n== a buyer asking by origin and mileage gets an answer, not a human ==")
        convo = fresh_conversation(db)
        _, calls = loop.run_turn(db, convo, "anything german under 100k miles?",
                                 provider=FakeProvider([
                                     call_tool("search_inventory", origin="german",
                                               max_mileage=100000),
                                     say("We have two German cars under 100,000 miles."),
                                 ]))
        german = calls[0]["result"].get("vehicles", [])
        check("origin is a real filter, not a keyword guess", bool(german),
              ", ".join(f"{v['make']} {v['model']}" for v in german))
        check("and every hit really is German",
              all(v["origin"] == "german" for v in german))
        check("the mileage cap applied",
              all((v["mileage"] or 0) <= 100000 for v in german))

        convo = fresh_conversation(db)
        _, calls = loop.run_turn(db, convo, "any Ferraris?", provider=FakeProvider([
            call_tool("search_inventory", origin="italian"),
            say("Nothing Italian on the lot right now."),
        ]))
        check("an empty result tells the model to answer, not escalate",
              "do not hand it to a person" in calls[0]["result"].get("guidance", ""),
              f"count={calls[0]['result'].get('count')}")

        print("\n== an invented argument name is refused, not ignored ==")
        convo = fresh_conversation(db)
        _, calls = loop.run_turn(db, convo, "third row please", provider=FakeProvider([
            call_tool("search_inventory", need="third row", max_price=25000),
            say("What sort of thing are you after?"),
        ]))
        check("the model is told the argument does not exist",
              any("has no argument" in str(c["result"].get("error", "")) for c in calls),
              str(calls[0]["result"])[:80] if calls else "")

        print("\n== a malformed tool argument does not kill the turn ==")
        convo = fresh_conversation(db)
        reply, calls = loop.run_turn(db, convo, "book me in", provider=FakeProvider([
            Completion(tool_calls=[ToolCall(id="c1", name="book_appointment",
                                            input={"__malformed__": "{not json"})]),
            say("I could not read that -- what day works for you?"),
        ]))
        check("the bad call became a tool error the model can see",
              any("error" in c["result"] for c in calls),
              str(calls[0]["result"])[:70] if calls else "")
        check("and the turn still produced a reply", bool(reply))

        print("\n== a policy question is answered from the dealership's own words ==")
        convo = fresh_conversation(db)
        _, calls = loop.run_turn(db, convo, "do you take trade-ins?", provider=FakeProvider([
            call_tool("answer_from_knowledge", question="do you take trade-ins?"),
            say("Yes -- bring the title and we'll appraise it while you wait."),
        ]))
        knowledge = [c for c in calls if c["name"] == "answer_from_knowledge"]
        check("the knowledge tool is reachable by the model", bool(knowledge))
        check("and it found the dealer's answer",
              bool(knowledge and knowledge[0]["result"].get("found")),
              knowledge[0]["result"].get("topic", "") if knowledge else "")

        convo = fresh_conversation(db)
        _, calls = loop.run_turn(db, convo, "doc fee?", provider=FakeProvider([
            call_tool("answer_from_knowledge", question="what is your doc fee?"),
            say("Our documentation fee is $189, the same on every vehicle."),
        ]))
        # The near-miss that made this worth fixing: "doc" and "fee" were being
        # dropped as short words, so this returned the deposit policy.
        check("a doc fee question does not return the deposit policy",
              calls[0]["result"].get("topic") == "Doc fee",
              calls[0]["result"].get("topic", "(none)"))

        convo = fresh_conversation(db)
        _, calls = loop.run_turn(db, convo, "?", provider=FakeProvider([
            call_tool("answer_from_knowledge", question="do you deliver to Alaska by hovercraft?"),
            say("Let me get a colleague to confirm that one."),
        ]))
        check("an unknown policy returns nothing rather than a plausible guess",
              calls[0]["result"].get("found") is False)

        print("\n== a handoff does not end the conversation ==")
        convo = fresh_conversation(db)
        _, calls = loop.run_turn(db, convo, "what's my out the door price?",
                                 provider=FakeProvider([
                                     call_tool("escalate_to_human",
                                               rule_key="out_the_door_price",
                                               reason="asked for an out-the-door total"),
                                     say("I've asked a colleague for the exact number. "
                                         "What's the best email to reach you on?"),
                                 ]))
        esc = calls[0]["result"]
        check("a rep was notified", esc.get("escalated") is True)
        db.refresh(convo)
        # This was the bug: escalating gagged Liner, so every later message got
        # "someone is picking this up" and the thread died with nobody watching.
        check("but Liner is not gagged -- only a rep pressing Take over does that",
              convo.agent_paused is False, f"agent_paused={convo.agent_paused}")
        check("and it is told to ask for a way to reach them",
              "ask for a name and an email" in esc.get("guidance", ""),
              f"reachable={esc.get('buyer_reachable')}")

        # Escalating again on the same conversation must not open a second
        # unclaimed row. It used to, and conversation_out read that back with
        # one_or_none() -- so a conversation escalated twice returned 500 for
        # the whole conversations list, taking the dealer's main page down.
        loop.run_turn(db, convo, "seriously, what's the total?", provider=FakeProvider([
            call_tool("escalate_to_human", rule_key="out_the_door_price",
                      reason="asked a second time"),
            say("Still waiting on a colleague -- what's the best email for you?"),
        ]))
        open_rows = (
            db.query(Escalation)
            .filter(Escalation.conversation_id == convo.id, Escalation.claimed_at.is_(None))
            .count()
        )
        check("escalating twice leaves one open handoff, not two",
              open_rows == 1, f"open={open_rows}")
        check("and the conversations list can still be serialised",
              conversation_out(convo, db).get("open_escalation") is not None)

        print("\n== the buyer ends it, and can have a summary ==")
        convo = fresh_conversation(db)
        _, calls = loop.run_turn(db, convo, "that's all thanks", provider=FakeProvider([
            call_tool("close_conversation",
                      summary="Wanted a third row under 25k. Showed the Sienna and Sorento.",
                      send_summary=True),
            say("Sent -- have a good evening."),
        ]))
        closed = calls[0]["result"]
        db.refresh(convo)
        check("the conversation is closed with a summary", bool(convo.summary),
              convo.summary[:50])
        check("and stamped with an end time", convo.ended_at is not None)
        check("an emailed summary with no address on file says so, rather than lying",
              closed.get("emailed") is False and "no address on file" in closed.get("note", ""),
              closed.get("note", "")[:60])

        print("\n== guards run on the live path too ==")
        convo = fresh_conversation(db)
        # A price with no search_inventory result behind it is exactly what the
        # guards exist to stop -- the model inventing a number.
        reply, calls = loop.run_turn(db, convo, "how much is the Accord?", provider=FakeProvider([
            say("The Accord is $18,400 out the door."),
        ]))
        check("an unsourced price did not reach the buyer",
              "$18,400" not in reply, reply[:70])

        # The retry note goes back as a user turn, because that is the only role
        # every vendor takes mid-conversation. A model read it as the buyer
        # objecting and opened with "You're right -- I shouldn't have mentioned
        # a price before it was looked up", apologising to someone who never
        # said that and never saw the draft.
        convo = fresh_conversation(db)
        retry = FakeProvider([
            say("The Accord is $18,400."),
            say("Let me look that up properly -- what's your budget?"),
        ])
        reply, _ = loop.run_turn(db, convo, "how much is the Accord?", provider=retry)
        check("a corrected draft is replaced, not escalated",
              "$18,400" not in reply and "budget" in reply, reply[:60])
        note = " ".join(
            str(m.get("content", "")) for m in retry.seen_messages[-1]
            if isinstance(m, dict) and m.get("role") == "user"
        )
        check("and the retry note says it is not the buyer talking",
              "not from the buyer" in note and "do not apologise" in note)

        # ...and the other half of that guard: the buyer's own budget, quoted
        # back. This shipped broken -- every "what do you have under $20,000"
        # tripped the guard twice and the buyer got the escalation line instead
        # of an answer, on every single turn. The number is in the buyer's
        # message and in the search that ran; it is not an invented price.
        convo = fresh_conversation(db)
        reply, calls = loop.run_turn(db, convo, "What do you have under $20,000?", provider=FakeProvider([
            call_tool("search_inventory", max_price=20000),
            say("A 2021 Toyota Corolla is the closest under $20,000 -- want the details?"),
        ]))
        check("the buyer's own budget, quoted back, is not an invented price",
              "under $20,000" in reply, reply[:70])
        check("and the search that grounded it really ran",
              any(c["name"] == "search_inventory" for c in calls))

        print("\n== the voice session, as far as it can honestly be checked ==")
        # The mint call needs a key this environment does not have and must not
        # invent -- and api.openai.com is refused by the egress proxy besides.
        # Everything either side of that one request is asserted here.
        from app.agent.prompts import build_system_prompt
        from app.api.settings import live_settings
        from app.config import settings as cfg
        from app.integrations.voice.openai_realtime import OpenAIRealtimeProvider
        from app.integrations.voice.base import UnconfiguredVoiceProvider
        from app.models import Dealership

        voice = OpenAIRealtimeProvider()
        if not (cfg.voice_provider_key or cfg.openai_api_key):
            try:
                voice.check()
                check("voice refuses to pretend it is configured", False, "check() passed")
            except Exception as exc:
                check("voice names the variable it wants rather than failing vaguely",
                      "OPENAI_API_KEY" in getattr(exc, "missing", []),
                      str(getattr(exc, "missing", exc)))

        # A key sitting in the environment for the chat agent must not switch
        # the phone on by itself. Turning a dealership's calls on is a decision.
        check("a key alone does not start answering the phone",
              isinstance(get_provider_for(""), UnconfiguredVoiceProvider))
        check("and choosing openai does", isinstance(
            get_provider_for("openai"), OpenAIRealtimeProvider))

        dealership = db.query(Dealership).first()
        spoken = build_system_prompt(db, dealership, live_settings(db), channel="voice")
        written = build_system_prompt(db, dealership, live_settings(db))
        check("a call gets the rules that only make sense out loud",
              "ON A PHONE CALL" in spoken and "Words only" in spoken)
        # A real call asked "when would you like to come in?", was told ten,
        # and answered "we have eleven". On a screen the card offers the times;
        # out loud nothing does unless the assistant is told to.
        check("and is told to offer real times rather than ask an open question",
              "check_availability first" in spoken and "two real times" in spoken)
        # It promised to read the address back and then booked without doing
        # it, which is how a lead ends up unreachable.
        check("and to wait for the email to be confirmed before booking",
              "wait for a yes" in spoken)
        # Saying goodbye is not hanging up. The line, and the buyer's
        # microphone, stay open until close_conversation is called.
        check("and that ending the call takes a tool call, not a farewell",
              "close_conversation in the same turn" in spoken)
        check("and chat does not -- there is a card on a screen there",
              "PHONE CALL" not in written)
        # One prompt with an addendum, not two prompts. Two is how the price
        # rule ends up stricter on one channel than the other.
        check("but both carry the rule that matters",
              "THE ONE RULE THAT MATTERS" in spoken
              and "THE ONE RULE THAT MATTERS" in written)
        check("and both carry the dealership's own facts",
              dealership.name in spoken and dealership.phone in spoken)

        body = voice.session_payload(spoken, tools.TOOL_DEFS)["session"]
        check("the session body is the shape the realtime API documents",
              body["type"] == "realtime" and bool(body["model"]), str(sorted(body)))
        # The nested audio object, not the older flat keys -- those are
        # rejected outright, and the failure is a 400 nobody can read.
        check("audio config is nested under session.audio",
              body["audio"]["output"]["voice"] == cfg.voice_voice
              and set(body["audio"]["input"]) >= {
                  "transcription", "noise_reduction", "turn_detection"},
              str(body["audio"]))
        # Without this the buyer's own words never reach us and the dealer's
        # transcript of the call is a monologue.
        check("and asks for the buyer's side to be transcribed",
              bool(body["audio"]["input"]["transcription"]["model"]),
              str(body["audio"]["input"].get("transcription")))
        # The hint everyone omits. Left out, the transcriber infers the
        # language from the audio -- and telephone-quality audio, which is what
        # a Bluetooth headset produces the moment its microphone opens, is
        # exactly where inference produces confident nonsense.
        check("with the language named rather than guessed from the audio",
              body["audio"]["input"]["transcription"].get("language") == cfg.voice_language,
              str(body["audio"]["input"]["transcription"].get("language")))
        # Cleans the input before either the transcriber or the turn detector
        # reads it. near_field is a headset; far_field on a close mic strips
        # the speech it should keep.
        check("and the input cleaned for the microphone it expects",
              body["audio"]["input"]["noise_reduction"]["type"]
              in {"near_field", "far_field"},
              str(body["audio"]["input"].get("noise_reduction")))
        # Gappy audio reads as silence to a fixed threshold, so the buyer gets
        # cut off mid-sentence and the model answers half a question -- which
        # from their side is indistinguishable from not being heard.
        turn = body["audio"]["input"]["turn_detection"]
        check("turn taking is judged, not merely timed",
              turn["type"] in {"semantic_vad", "server_vad"}, str(turn))
        # Talking over an assistant that keeps going is the single most
        # infuriating thing a phone bot does, and it is one flag.
        check("and the buyer can always interrupt",
              turn.get("interrupt_response") is True, str(turn))
        check("every tool is offered on a call too, not a subset",
              {t["name"] for t in body["tools"]} == {t["name"] for t in tools.TOOL_DEFS},
              f"{len(body['tools'])} tools")
        check("in the flat shape realtime takes",
              all(t["type"] == "function" and "parameters" in t for t in body["tools"]))
        # One conversion, shared with the Responses path. A second copy is how
        # a tool ends up offered on one channel and not the other.
        check("and it is the same conversion the chat loop uses",
              {t["name"] for t in body["tools"]} == {t["name"] for t in _openai_tools()})
        check("the instructions really are the dealership's prompt",
              body["instructions"] == spoken)

        # A realtime call bills the whole conversation so far as input on every
        # turn. Left alone that grows without limit and most of a long call's
        # bill is re-reading its own beginning; these are the only two brakes.
        check("a turn cannot run away in output audio, the dearest stream",
              body["max_output_tokens"] == cfg.voice_max_output_tokens,
              str(body.get("max_output_tokens")))
        check("and the conversation stops growing without limit",
              body["truncation"]["type"] == "retention_ratio"
              and 0.0 < body["truncation"]["retention_ratio"] <= 1.0,
              str(body.get("truncation")))

        # A model told never to speak the customer's side. A real call opened
        # with "Hi! I'm looking for a compact SUV" -- in the assistant's voice
        # -- and then answered itself for four turns.
        check("the model is told never to speak the customer's side",
              "Never say the customer's side" in spoken)
        check("and to stay quiet rather than fill a silence",
              "stay silent" in spoken)
        # The voice rules are appended to a prompt that is already long, and
        # every token of them is re-read on every turn of every call. Kept
        # short on purpose; this is the tripwire if it creeps back.
        from app.agent.prompts import VOICE_ADDENDUM
        check("and the whole addendum stays short enough to reread every turn",
              len(VOICE_ADDENDUM) < 1500, f"{len(VOICE_ADDENDUM)} chars")

        # Switching to the cheaper model must re-price the report too. Pinned
        # separately, changing one line of .env would leave this dashboard
        # charging flagship rates for mini traffic -- three times over, with
        # nothing on the page to contradict it.
        from app.integrations.voice.openai_realtime import rates_for
        flagship, _ = rates_for("gpt-realtime")
        mini, _ = rates_for("gpt-realtime-mini")
        check("the mini model is priced as the mini model",
              mini["audio_in"] < flagship["audio_in"] / 2,
              f"{mini['audio_in']} vs {flagship['audio_in']}")
        # A dated snapshot is still its family; another tier is not. A plain
        # prefix match makes a future gpt-realtime-nano a flagship and charges
        # it at three times its cost, silently.
        check("a dated snapshot is priced as its family",
              rates_for("gpt-realtime-mini-2.1")[0]["audio_in"] == mini["audio_in"])
        check("but an unknown tier is reported unpriced, not guessed at",
              rates_for("gpt-realtime-nano")[1] is False
              and rates_for("something-else")[1] is False)

        # Transcribing the buyer is billed on top of the call and buys a
        # readable record, nothing more -- the model hears the audio directly.
        # So it is the one part that can be switched off without changing what
        # the assistant can do, and switching it off must really remove it.
        from app.config import settings as live_cfg
        was = live_cfg.voice_transcribe
        try:
            live_cfg.voice_transcribe = False
            check("transcription can be switched off, since it bills separately",
                  "transcription" not in voice.session_payload(spoken, [])["session"]["audio"]["input"])
        finally:
            live_cfg.voice_transcribe = was

        return report()
    finally:
        db.close()


def get_provider_for(name: str):
    """The registry's choice under a given VOICE_PROVIDER, restored after."""
    from app.config import settings as cfg
    from app.integrations.registry import get_voice_provider

    was = cfg.voice_provider
    try:
        cfg.voice_provider = name
        return get_voice_provider()
    finally:
        cfg.voice_provider = was


def report() -> int:
    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("Live loop plumbing verified against a fake provider.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
