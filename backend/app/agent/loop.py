"""The live tool loop -- one buyer turn against a real model.

This is what makes the assistant *unscripted*. The stub agent walks
``conversations.stage`` and assembles replies from tool results, so it can only
answer questions someone anticipated. Here the model reads the conversation,
decides which tools to call and writes its own words.

What does **not** change when you switch it on:

* **The tools.** Same six executors, same rows written. A do-not-discuss
  vehicle is filtered inside ``search_inventory``, so it never reaches the
  model regardless of what the model is told.
* **The guards.** Every reply is checked for a price or a claim it cannot
  source. One violation buys a corrective retry; a second escalates to a
  human. That policy lives here, once, for every vendor.

Vendor differences live in ``providers.py``. This file never names OpenAI or
Anthropic, which is what stops a second vendor becoming a second copy of the
loop with the guards quietly missing from it.

# PLACEHOLDER(llm): the vendor calls in providers.py have never run against a
# real endpoint -- there is no API key in this environment. The turn loop
# below, including tool dispatch, the malformed-argument path, the guard retry
# and the escalation, IS exercised on every `make smoke` run against a fake
# provider (scripts/agent_loop_check.py). So the plumbing is tested and the
# HTTP call is not. Expect to fix a wire-format detail on the first live run,
# not the shape of the conversation.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.agent import guards, tools
from app.agent.prompts import build_system_prompt
from app.agent.providers import Provider, get_provider
from app.api.settings import live_settings
from app.models import Conversation, Dealership, Message

log = logging.getLogger("liner.agent")

# A turn that has called tools six times is not converging, and every round is
# another paid request with the buyer watching a spinner.
MAX_TOOL_ROUNDS = 6


def _history(db: Session, convo: Conversation) -> list[dict]:
    rows = (
        db.query(Message)
        .filter_by(conversation_id=convo.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    history: list[dict] = []
    for m in rows:
        if m.role == "buyer":
            history.append({"role": "user", "content": m.content})
        elif m.content:
            # A rep's message is shown to the model as its own prior turn: from
            # the buyer's side both came from the dealership.
            history.append({"role": "assistant", "content": m.content})
    return history


def run_turn(
    db: Session, convo: Conversation, text: str, provider: Provider | None = None
) -> tuple[str, list[dict]]:
    """One buyer turn. Returns (reply, tool_calls).

    ``provider`` is injectable so the loop can be driven offline against a fake
    one. That is the only reason this path is testable without a key.
    """
    provider = provider or get_provider()
    dealership = db.query(Dealership).first()
    system = build_system_prompt(db, dealership, live_settings(db))

    messages = _history(db, convo)
    # The caller normally persists the buyer's message before getting here, so
    # it is already the last entry. Depend on that and a caller who forgets
    # sends an empty conversation, which the vendor rejects with a 400 about a
    # missing `input` -- an error that says nothing about the real mistake.
    text = (text or "").strip()
    if text and (not messages or messages[-1] != {"role": "user", "content": text}):
        messages.append({"role": "user", "content": text})
    if not messages:
        raise ValueError(
            "run_turn called with no conversation and no text -- there is nothing "
            "to send."
        )

    calls: list[dict] = []
    attempt = 1

    while True:
        rounds = 0
        completion = None

        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            completion = provider.complete(system, messages)
            if not completion.tool_calls:
                break

            results: list[tuple] = []
            for call in completion.tool_calls:
                try:
                    result = tools.execute(db, convo, call.name, call.input, call.id)
                    is_error = False
                except tools.ToolError as exc:
                    # Handed back to the model rather than raised: an unknown
                    # VIN or a slot outside opening hours is something it can
                    # recover from in the next round.
                    result = {"error": str(exc)}
                    is_error = True
                calls.append({"name": call.name, "input": call.input, "result": result})
                results.append((call, result, is_error))

            provider.append_tool_round(messages, completion, results)
        else:
            log.warning(
                "conversation %s hit %d tool rounds without settling", convo.id, MAX_TOOL_ROUNDS
            )

        final_text = (completion.text if completion else "").strip()

        # Guards run in every mode, live included. If a live turn can slip an
        # unsourced price past them, the guard has a hole.
        assistant_turns = sum(1 for m in messages if m.get("role") == "assistant")
        verdict = guards.run_guards(
            final_text,
            [c["result"] for c in calls],
            channel=convo.channel,
            attempt=attempt,
            assistant_turns=assistant_turns,
            booked=convo.stage == "booked",
        )

        if verdict.ok:
            return verdict.text, calls

        if verdict.should_escalate:
            log.warning("guard escalation on conversation %s: %s", convo.id, verdict.violations)
            try:
                tools.execute(db, convo, "escalate_to_human", {
                    "rule_key": "asks_for_manager",
                    "reason": "Liner could not source a claim it was about to make: "
                              + "; ".join(verdict.violations),
                }, f"guard-{convo.id}-{len(calls)}")
            except tools.ToolError:
                pass
            return verdict.text, calls

        # First violation: one corrective retry, with the specific complaint.
        log.info("guard retry on conversation %s: %s", convo.id, verdict.violations)
        attempt += 1
        messages.append({"role": "assistant", "content": final_text or "(empty)"})
        messages.append({"role": "user", "content": guards.corrective_note(verdict.violations)})
