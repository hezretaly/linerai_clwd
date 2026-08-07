#!/usr/bin/env python3
"""One real turn against the configured model, with the error printed in full.

The buyer-facing chat deliberately shows a buyer "someone will pick this up"
rather than a stack trace. That is right for a buyer and useless for you, so
this is the operator's version of the same call: it prints what the vendor
actually said.

    make agent-ping
    make agent-ping Q="do you take trade-ins?"

Costs one request. Reads the same .env the server does, so it fails in exactly
the way the server is failing.
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Conversation  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    question = os.environ.get("Q") or "Do you take trade-ins?"

    print("\n== configuration ==")
    print(f"  LLM_MODE      {settings.llm_mode}")
    print(f"  LLM_PROVIDER  {settings.llm_provider}")
    print(f"  model         {settings.openai_model}")
    print(f"  reasoning     {settings.openai_reasoning_effort}")
    print(f"  max output    {settings.openai_max_output_tokens}")
    key = settings.openai_api_key
    # Never print a key. Enough to tell "absent" from "present but wrong",
    # which is the distinction that matters when nothing works.
    print(f"  OPENAI_API_KEY {'set, ' + str(len(key)) + ' chars, ends ' + key[-4:] if key else 'NOT SET'}")

    try:
        import openai
        print(f"  openai package {openai.__version__}")
        has_responses = hasattr(openai.OpenAI(api_key='x'), "responses")
        print(f"  responses API  {'available' if has_responses else 'MISSING -- upgrade the openai package'}")
    except ImportError:
        print("  openai package NOT INSTALLED -- run `make install`")
        return 1

    if settings.llm_mode != "live":
        print("\nLLM_MODE is not 'live', so the server is answering from the stub.")
        print("This ping talks to the model anyway, to prove the credentials work.")

    print(f"\n== asking: {question!r} ==")
    db = SessionLocal()
    try:
        from app.agent import loop
        from app.agent.runner import record_buyer_message

        convo = Conversation(channel="chat", stage="opening")
        db.add(convo)
        db.commit()
        # Persist the message exactly as the chat endpoint does. Skipping this
        # sent an empty conversation and produced a 400 the real chat never
        # hits -- a diagnostic that reproduces the wrong failure is worse than
        # none, because it sends you fixing the wrong thing.
        record_buyer_message(db, convo, question)
        reply, calls = loop.run_turn(db, convo, question)
    except Exception:
        print("\n-- FAILED. The full error follows; this is what the buyer saw as")
        print("-- 'Something went wrong answering that'.\n")
        traceback.print_exc()
        print("\nCommon causes:")
        print("  * 401  -- the key is wrong, revoked, or from a different org")
        print("  * 404  -- the model name is not available to this account;")
        print("            try OPENAI_MODEL=gpt-5.4-mini or gpt-4o")
        print("  * 429  -- no credit on the account, or rate limited")
        print("  * 400  -- a request-shape problem; the message names the field")
        return 1
    finally:
        db.close()

    print("\n-- OK\n")
    for call in calls:
        print(f"  tool: {call['name']}({call['input']})")
    print(f"\n  reply: {reply}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
