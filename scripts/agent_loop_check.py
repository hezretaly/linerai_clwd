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
from app.agent.providers import Completion, ToolCall, _openai_tools  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Conversation, Vehicle  # noqa: E402


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
        provider = FakeProvider([
            call_tool("search_inventory", keywords="third row", max_price=25000),
            # The price is quoted back from what the tool returned, which is the
            # only way a reply survives the guards.
            say("We have a 2019 Kia Sorento EX at $22,900 with third-row seating. "
                "Want to come and see it?"),
        ])
        reply, calls = loop.run_turn(db, convo, "Something with a third row under 25k",
                                     provider=provider)
        check("the tool actually ran", any(c["name"] == "search_inventory" for c in calls))
        found = calls[0]["result"].get("vehicles", []) if calls else []
        check("it returned real inventory", bool(found), f"{len(found)} vehicles")
        check("ranked on the need, not just the price cap",
              any("Sorento" in v.get("model", "") or (v.get("seats") or 0) >= 7 for v in found),
              ", ".join(v.get("model", "?") for v in found))
        check("a price taken from the tool result reaches the buyer",
              "$22,900" in reply, reply[:70])

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
        if blocked is None:
            blocked = db.query(Vehicle).first()
            blocked.rule_discuss = False
            db.commit()
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

        print("\n== guards run on the live path too ==")
        convo = fresh_conversation(db)
        # A price with no search_inventory result behind it is exactly what the
        # guards exist to stop -- the model inventing a number.
        reply, calls = loop.run_turn(db, convo, "how much is the Accord?", provider=FakeProvider([
            say("The Accord is $18,400 out the door."),
        ]))
        check("an unsourced price did not reach the buyer",
              "$18,400" not in reply, reply[:70])

        return report()
    finally:
        db.close()


def report() -> int:
    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("Live loop plumbing verified against a fake provider.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
