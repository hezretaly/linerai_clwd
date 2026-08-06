"""The live Anthropic tool loop.

# PLACEHOLDER(anthropic): unverified. There is no ANTHROPIC_API_KEY in the
# build environment, so this code path has never executed. Its shape follows the
# Messages API tool-use contract, and the tool executors and guards it calls are
# the same ones the stub agent exercises -- but treat the loop itself as
# unreviewed until someone runs it with a real key.
#
# Switching on: set ANTHROPIC_API_KEY and LLM_MODE=live. Nothing else changes.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agent import guards, tools
from app.agent.prompts import build_system_prompt
from app.api.settings import live_settings
from app.config import settings
from app.integrations.base import NotConfigured
from app.models import Conversation, Dealership, Message

log = logging.getLogger("liner.agent")

MAX_TOOL_ROUNDS = 6


def _client():
    if not settings.anthropic_api_key:
        raise NotConfigured(
            "llm", ["ANTHROPIC_API_KEY"],
            "LLM_MODE=live requires an Anthropic API key. Leave LLM_MODE=stub to run the "
            "scripted agent instead.",
        )
    from anthropic import Anthropic

    return Anthropic(api_key=settings.anthropic_api_key)


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


def run_turn(db: Session, convo: Conversation, text: str) -> tuple[str, list[dict]]:
    """One buyer turn against the live model. Returns (text, tool_calls)."""
    client = _client()
    dealership = db.query(Dealership).first()
    system = build_system_prompt(db, dealership, live_settings(db))

    messages = _history(db, convo)
    calls: list[dict] = []
    attempt = 1

    while True:
        rounds = 0
        final_text = ""

        while rounds < MAX_TOOL_ROUNDS:
            rounds += 1
            response = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1024,
                system=system,
                tools=tools.TOOL_DEFS,
                messages=messages,
            )

            tool_uses = [b for b in response.content if getattr(b, "type", "") == "tool_use"]
            text_blocks = [
                b.text for b in response.content if getattr(b, "type", "") == "text"
            ]
            final_text = "\n".join(text_blocks).strip()

            if not tool_uses:
                break

            messages.append({"role": "assistant", "content": response.content})
            results_block = []
            for use in tool_uses:
                try:
                    result = tools.execute(db, convo, use.name, dict(use.input), use.id)
                    is_error = False
                except tools.ToolError as exc:
                    result = {"error": str(exc)}
                    is_error = True
                calls.append({"name": use.name, "input": dict(use.input), "result": result})
                results_block.append({
                    "type": "tool_result",
                    "tool_use_id": use.id,
                    "content": json.dumps(result, default=str),
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": results_block})

        # Guards run on every mode, live included.
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

        # First violation: one corrective retry.
        log.info("guard retry on conversation %s: %s", convo.id, verdict.violations)
        attempt += 1
        messages.append({"role": "assistant", "content": final_text or "(empty)"})
        messages.append({"role": "user", "content": guards.corrective_note(verdict.violations)})
