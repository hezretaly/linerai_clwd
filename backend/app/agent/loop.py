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


def _role_of(item) -> str:
    """The role of a transcript entry, whatever shape it is.

    The list is not homogeneous. Entries we build are plain dicts, but a
    vendor's own turn goes back verbatim -- and for a reasoning model that
    includes ResponseReasoningItem objects, which are pydantic models with no
    .get(). Assuming dicts here crashed every live turn *after* a successful
    200 from the API, which made it look like the integration was broken when
    it was working perfectly.
    """
    if isinstance(item, dict):
        return item.get("role", "") or ""
    return getattr(item, "role", "") or ""


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
    db: Session,
    convo: Conversation,
    text: str,
    provider: Provider | None = None,
    *,
    channel: str = "",
    addendum: str = "",
) -> tuple[str, list[dict]]:
    """One buyer turn. Returns (reply, tool_calls).

    ``provider`` is injectable so the loop can be driven offline against a fake
    one. That is the only reason this path is testable without a key.

    ``channel`` picks the addendum appended to the prompt -- a screen, a phone
    call, or an inbox. It defaults to the conversation's own, so a caller
    cannot forget: an email answered with the chat rules offers a booking card
    nobody can see.

    ``addendum`` is one more block for *this turn only*, and it exists so a
    situation the model needs to know about does not have to arrive disguised
    as the buyer speaking. The follow-up on a quiet buyer is the case: sent as
    a user message it makes the model answer somebody who said nothing, which
    is the same failure the guard's retry note had to be rewritten for.
    """
    provider = provider or get_provider()
    dealership = db.query(Dealership).first()
    system = build_system_prompt(
        db, dealership, live_settings(db), channel=channel or convo.channel or "chat"
    )
    if addendum:
        # Last, like every other addendum here, so it is what was read most
        # recently where it narrows something above it.
        system = f"{system}\n{addendum.strip()}"

    messages = _history(db, convo)
    # The caller normally persists the buyer's message before getting here, so
    # it is already the last entry. Depend on that and a caller who forgets
    # sends an empty conversation, which the vendor rejects with a 400 about a
    # missing `input` -- an error that says nothing about the real mistake.
    text = (text or "").strip()
    last = messages[-1] if messages else None
    if text and last != {"role": "user", "content": text}:
        messages.append({"role": "user", "content": text})
    if not messages:
        raise ValueError(
            "run_turn called with no conversation and no text -- there is nothing "
            "to send."
        )

    # Everything the buyer has actually typed, captured before the retry loop
    # can append anything. The corrective note below quotes the offending
    # numbers back ("unsourced price $20,000"); folding that into the grounding
    # set would let a second draft launder exactly the claim the first was
    # rejected for.
    buyer_text = " ".join(
        m["content"] for m in messages if isinstance(m, dict) and m.get("role") == "user"
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
        assistant_turns = sum(1 for m in messages if _role_of(m) == "assistant")
        verdict = guards.run_guards(
            final_text,
            [c["result"] for c in calls],
            channel=convo.channel,
            attempt=attempt,
            assistant_turns=assistant_turns,
            booked=convo.stage == "booked",
            tool_inputs=[c["input"] for c in calls if isinstance(c["input"], dict)],
            buyer_text=buyer_text,
            # A car the model named that no tool ever returned. The executor
            # cannot serve a sold one; nothing stopped the model mentioning it.
            makes=tools.known_makes(db),
            prior_results=tools.earlier_results(db, convo),
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


# --------------------------------------------------------------------------
# Drafting for a person to read
# --------------------------------------------------------------------------

def draft_text(
    db: Session,
    convo: Conversation,
    *,
    brief: str,
    provider: Provider | None = None,
) -> tuple[str, list[str]]:
    """Write something a **rep** will read, decide on, and maybe send.

    Returns `(text, violations)`. A non-empty `violations` means the guards
    refused the draft twice and the rep is shown why instead of being handed
    a claim nobody could source.

    **No tools, and that is the whole safety property.** `run_turn` exists to
    answer a buyer, and answering a buyer legitimately means *doing* things:
    `tools.execute` dispatches to `book_appointment`, `close_conversation`,
    `escalate_to_human` and `save_captured_fields`, which write rows, emit
    events and mint leads through `attach_lead`. A draft is not an action --
    nobody has agreed to anything yet -- so running the buyer loop to produce
    one would book the appointment the draft merely *offers*. The schema is
    withheld at the vendor (`offer_tools=False`) rather than the results
    being ignored, because ignoring a tool call still means it ran.

    **The guards are shared, and that is the other half.** This is not a
    second turn loop: everything about vendors still lives in
    `providers.py`, and `guards.run_guards` is the same call `run_turn`
    makes, with the same corrective retry. A rep can send a draft, so a price
    nothing sourced is exactly as wrong here as in a chat bubble. What is
    deliberately *not* shared is the escalation on a second failure -- that
    writes a handoff row, and a rep asking for a draft has not escalated
    anything. They are told, and they can write it themselves.

    The context comes in as `brief` rather than being fetched here: what a
    draft should know is a product question -- the thread, the car in focus,
    the captured fields, the dealership's own tone -- and `app/email_draft.py`
    composes it from rows, deterministically, for the same reason
    `app/recap.py` does.
    """
    provider = provider or get_provider()
    # **Not the buyer assistant's prompt.** That one says who Liner is and
    # that a colleague will pick up whatever it cannot answer -- correct for
    # Liner, and exactly wrong for an email a rep sends under their own name:
    # drafts came back first person in one sentence and "a colleague can
    # discuss the price" in the next, as though the writer were somebody
    # else. The facts it carried are all in the brief; the pricing posture is
    # the one rule that has to travel, and it does, in the dealership block.
    system = f"{DRAFT_SYSTEM}\n\n{brief.strip()}"

    # The transcript, so the draft can refer to what was actually said. The
    # instruction itself is the last user turn, which is what a model reads
    # most recently -- the same reasoning as every addendum here.
    messages = _history(db, convo)
    messages.append({"role": "user", "content": DRAFT_REQUEST})

    buyer_text = " ".join(
        m["content"] for m in messages if isinstance(m, dict) and m.get("role") == "user"
    )

    attempt = 1
    while True:
        completion = provider.complete(system, messages, offer_tools=False)
        text = (completion.text or "").strip()

        verdict = guards.run_guards(
            text,
            # No tool results, because no tools ran. The grounding a draft
            # gets is what the conversation was already told --
            # `earlier_results` is the same set the voice guard reads, so a
            # car found three turns ago is still a car this may mention.
            [],
            channel="email",
            attempt=attempt,
            assistant_turns=sum(1 for m in messages if _role_of(m) == "assistant"),
            booked=convo.stage == "booked",
            tool_inputs=[],
            buyer_text=buyer_text,
            makes=tools.known_makes(db),
            prior_results=tools.earlier_results(db, convo),
        )
        if verdict.ok:
            return verdict.text, []
        if attempt > 1:
            # Twice is enough. The rep sees the complaint rather than a draft
            # carrying a claim the guard would not let a buyer read.
            log.info("draft refused on conversation %s: %s", convo.id, verdict.violations)
            return "", list(verdict.violations)
        attempt += 1
        messages.append({"role": "assistant", "content": text or "(empty)"})
        messages.append({"role": "user", "content": guards.corrective_note(verdict.violations)})


#: The last user turn on a draft request. It says who is asking and what they
#: will do with the answer, because a model handed a transcript and nothing
#: else writes the next message *to the buyer* -- and this one is read by a
#: rep who has not decided to send anything yet.
#: The last user turn of a draft. **A subject line is asked for** -- the
#: composer's subject box stayed empty after every draft, so a rep sent a
#: body under no subject or wrote one by hand every time -- and asked for in a
#: fixed first line, `Subject: ...`, which `email_draft.split_subject` reads
#: back off. One line in the same text rather than a second request, so the
#: guards read the subject with the body: a price in a subject line is as
#: sayable to a buyer as one in the email under it.
DRAFT_REQUEST = (
    "Draft this email now, in the first person, as the team member named in "
    "the brief -- it goes out under their name. Start with exactly one line "
    "`Subject: ` followed by a short subject, then a blank line, then the "
    "email body, ending with the closing the brief asks for. Do not say that "
    "you are an assistant, and do not promise anything the team has not "
    "agreed to."
)


#: The whole system prompt of a draft, ahead of `email_draft.brief`. Short on
#: purpose: the brief is the data, and this is only who is speaking and how.
DRAFT_SYSTEM = """You write emails for a member of a car dealership's sales team. They read
what you write, may edit it, and send it themselves under their own name. It is
their email, not an assistant's.

Write in the first person as the person named under WHO IS WRITING: "I" for
them and "we" for the dealership. Never mention Liner, an assistant or AI.
Anything that needs checking, they check: "I'll confirm that and come back to
you", never "a colleague will". Anything that happens at the dealership, they
are part of: "when you come in, I can go through the price with you", never
"someone can".

State only facts written in the brief below -- no price, mileage, feature,
vehicle or policy that is not there. Plain text, no markdown. Two or three
short paragraphs; a buyer reads this on a phone."""
