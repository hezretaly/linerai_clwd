"""A scripted Provider, for exercising the live loop without an API key.

The live loop is the one path in this system that cannot be tested the normal
way: it needs a paid endpoint that does not exist in CI or on a laptop. The
usual answer is to leave it untested and hope, which is how the guards quietly
stop running on the path that most needs them.

Instead: the loop takes an injectable provider, and this one returns a canned
script of ``Completion`` objects. Everything between the buyer's message and
the reply is then real -- tool dispatch, tool errors, the malformed-argument
path, the guard verdict, the corrective retry, the escalation. Only the HTTP
call is absent.

This is a test double and says so. It is never reachable from a running server:
``get_provider()`` returns it for no value of ``LLM_PROVIDER``, and nothing
imports it outside the checks in ``scripts/``.
"""

from __future__ import annotations

import json

from app.agent.providers import Completion, Provider, ToolCall


class FakeProvider(Provider):
    """Replays ``script`` one entry per completion, then repeats the last."""

    name = "fake"
    model = "fake"

    def __init__(self, script: list[Completion]) -> None:
        if not script:
            raise ValueError("FakeProvider needs at least one Completion")
        self.script = script
        self.calls = 0
        self.seen_systems: list[str] = []
        self.seen_messages: list[list[dict]] = []

    def complete(self, system: str, messages: list[dict]) -> Completion:
        self.seen_systems.append(system)
        # Copied, because the loop mutates the list after this returns and a
        # shared reference would make every recorded turn look identical.
        self.seen_messages.append([dict(m) for m in messages])
        index = min(self.calls, len(self.script) - 1)
        self.calls += 1
        return self.script[index]

    def append_tool_round(self, messages, completion, results) -> None:
        # The Responses API's shape, since that is what LLM_PROVIDER defaults
        # to. The property worth asserting is that every function_call is
        # echoed back before its output: omit one and the next request fails
        # with "No tool call found for function call output with call_id",
        # which is exactly the bug this double exists to catch.
        messages.extend(
            {"type": "function_call", "call_id": c.id, "name": c.name,
             "arguments": json.dumps(c.input, default=str)}
            for c in completion.tool_calls
        )
        for call, result, _is_error in results:
            messages.append({
                "type": "function_call_output",
                "call_id": call.id,
                "output": json.dumps(result, default=str),
            })


def say(text: str) -> Completion:
    return Completion(text=text)


def call_tool(name: str, **kwargs) -> Completion:
    return Completion(text="", tool_calls=[ToolCall(id=f"call_{name}", name=name, input=kwargs)])
