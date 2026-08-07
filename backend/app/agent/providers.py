"""Model vendors, behind one interface.

The turn loop and the guards must not care who is answering. Everything that
differs between vendors is the wire format -- how tools are declared, how a
tool call comes back, how a tool result is handed in -- and all of it lives
here. ``loop.py`` above this line is vendor-neutral and is the only place the
guard/retry policy exists.

Adding a third vendor is a class here and a name in ``LLM_PROVIDER``. It is not
a second copy of the turn loop, because a second copy is how one of them
quietly stops running the guards.

Neither client is imported at module level: the packages are optional, and a
missing one must surface as a typed 503 naming the setting, not an ImportError
at startup.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.agent import tools
from app.config import settings
from app.integrations.base import NotConfigured

log = logging.getLogger("liner.agent")


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Completion:
    """One model response, normalised."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The vendor's own representation of this turn, kept verbatim so it can be
    # appended to the transcript. Re-serialising it ourselves is how subtle
    # shape bugs get in.
    raw: Any = None


class Provider:
    """What ``loop.py`` is allowed to assume about a vendor."""

    name = ""
    model = ""

    def complete(self, system: str, messages: list[dict]) -> Completion:
        raise NotImplementedError

    def append_tool_round(
        self, messages: list[dict], completion: Completion, results: list[tuple[ToolCall, dict, bool]]
    ) -> None:
        """Append the assistant's tool call and its results, vendor-shaped."""
        raise NotImplementedError


# --------------------------------------------------------------------------
# OpenAI -- the Responses API, not chat completions
# --------------------------------------------------------------------------
#
# The default model is a gpt-5.x *reasoning* model, and that rules out the
# older endpoint on two counts:
#
#   * Reasoning models reject `max_tokens` outright ("Extra inputs are not
#     permitted -- use max_completion_tokens instead").
#   * OpenAI's own guidance is that reasoning models belong on /v1/responses,
#     and function tools combined with a reasoning effort are restricted on
#     /v1/chat/completions for some models in the family.
#
# So this speaks Responses: flat tool definitions, `function_call` items in the
# output, `function_call_output` items carrying results back.

def _openai_tools() -> list[dict]:
    """TOOL_DEFS is written in Anthropic's shape; this is the same content in
    the Responses API's. One source of truth, converted, rather than two lists
    that drift -- a tool present in one and not the other is invisible until a
    live turn.

    Note the flat shape: Responses puts name/parameters at the top level, where
    chat completions nested them under a "function" key.
    """
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }
        for t in tools.TOOL_DEFS
    ]


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise NotConfigured(
                "llm", ["OPENAI_API_KEY"],
                "LLM_MODE=live with LLM_PROVIDER=openai requires an OpenAI API key. "
                "Leave LLM_MODE=stub to run the scripted agent instead.",
            )
        try:
            from openai import OpenAI
        except ImportError:  # a git pull without `make install`
            raise NotConfigured(
                "llm", ["openai package"],
                "The openai package is not installed in this environment. "
                "Run `make install` (as the user the service runs as), then "
                "restart. A bare ImportError here would surface to the buyer as "
                "a generic failure with no clue what to do.",
            ) from None

        kwargs: dict = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        self.client = OpenAI(**kwargs)
        self.model = settings.openai_model

    def complete(self, system: str, messages: list[dict]) -> Completion:
        request: dict = {
            "model": self.model,
            # `instructions` is the Responses API's system prompt.
            "instructions": system,
            "input": messages,
            "tools": _openai_tools(),
            "max_output_tokens": settings.openai_max_output_tokens,
        }
        effort = (settings.openai_reasoning_effort or "").strip().lower()
        if effort and effort != "none":
            request["reasoning"] = {"effort": effort}

        response = self.client.responses.create(**request)

        calls = []
        for item in response.output or []:
            if getattr(item, "type", "") != "function_call":
                continue
            # Arguments arrive as a JSON *string*, and a model can emit
            # malformed JSON. Crashing here would lose the whole turn, so it
            # becomes a tool error the model can see and correct next round.
            try:
                parsed = json.loads(item.arguments or "{}")
            except ValueError:
                parsed = {"__malformed__": item.arguments}
            calls.append(ToolCall(id=item.call_id, name=item.name, input=parsed))

        text = (getattr(response, "output_text", "") or "").strip()

        # Reasoning tokens come out of the same budget as the reply, so a
        # ceiling that is too low returns nothing at all rather than something
        # short. Silence would look like the model having no answer.
        if not text and not calls and getattr(response, "status", "") == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "")
            log.warning(
                "empty completion from %s: status=incomplete reason=%s. "
                "Raise OPENAI_MAX_OUTPUT_TOKENS (currently %s) or lower "
                "OPENAI_REASONING_EFFORT (currently %s).",
                self.model, reason, settings.openai_max_output_tokens, effort,
            )

        return Completion(text=text, tool_calls=calls, raw=response.output)

    def append_tool_round(self, messages, completion, results) -> None:
        # The model's own function_call items go back verbatim, before their
        # outputs. Without them the next request fails with "No tool call found
        # for function call output with call_id".
        messages.extend(completion.raw or [])
        for call, result, _is_error in results:
            messages.append({
                "type": "function_call_output",
                "call_id": call.id,
                "output": json.dumps(result, default=str),
            })


# --------------------------------------------------------------------------
# Anthropic (messages + tool use)
# --------------------------------------------------------------------------

class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise NotConfigured(
                "llm", ["ANTHROPIC_API_KEY"],
                "LLM_MODE=live with LLM_PROVIDER=anthropic requires an Anthropic API key. "
                "Leave LLM_MODE=stub to run the scripted agent instead.",
            )
        try:
            from anthropic import Anthropic
        except ImportError:
            raise NotConfigured(
                "llm", ["anthropic package"],
                "The anthropic package is not installed. Run `make install`.",
            ) from None

        self.client = Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.anthropic_model

    def complete(self, system: str, messages: list[dict]) -> Completion:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            tools=tools.TOOL_DEFS,
            messages=messages,
        )
        calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in response.content
            if getattr(b, "type", "") == "tool_use"
        ]
        text = "\n".join(
            b.text for b in response.content if getattr(b, "type", "") == "text"
        ).strip()
        return Completion(text=text, tool_calls=calls, raw=response.content)

    def append_tool_round(self, messages, completion, results) -> None:
        messages.append({"role": "assistant", "content": completion.raw})
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(result, default=str),
                    "is_error": is_error,
                }
                for call, result, is_error in results
            ],
        })


PROVIDERS = {"openai": OpenAIProvider, "anthropic": AnthropicProvider}


def get_provider() -> Provider:
    factory = PROVIDERS.get(settings.llm_provider.lower())
    if factory is None:
        raise NotConfigured(
            "llm", ["LLM_PROVIDER"],
            f"LLM_PROVIDER={settings.llm_provider!r} is not one of "
            f"{', '.join(sorted(PROVIDERS))}.",
        )
    return factory()


def provider_key_setting() -> str:
    """The env var this provider needs, for the not-configured banner."""
    return {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(
        settings.llm_provider.lower(), "LLM_PROVIDER"
    )


def provider_key_present() -> bool:
    return bool(
        {"openai": settings.openai_api_key, "anthropic": settings.anthropic_api_key}.get(
            settings.llm_provider.lower(), ""
        )
    )
