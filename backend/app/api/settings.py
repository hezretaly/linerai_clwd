"""Liner setup: the assistant's one prompt, behaviour, handoff rules,
knowledge, rails.

Draft vs live is the point. An edit is a draft until it is published, otherwise
a tweak silently changes buyer-facing behaviour mid-conversation (§18.2).

**One prompt, in plain words, and nothing else of the model's instructions is
served here.** The rules, the dealership facts, what each channel adds and the
writing assistant are product code (`agent/prompts.py`); a manager who is not
technical edits how Liner treats their buyers, never tool mechanics.

**Except the credit application link**, which is a fact about the dealership
rather than a change to how Liner talks: it has its own endpoint and takes
effect when it is saved (`put_credit_link`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
import re

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dealership, require_manager
from app.db import get_db, utcnow
from app.escalations import fired_counts
from app.models import (
    AssistantPrompt,
    AssistantSettings,
    Dealership,
    HandoffRule,
    KnowledgeEntry,
    Rail,
    User,
)
from app.schemas.serialize import handoff_rule_out, knowledge_out, rail_out, settings_out

router = APIRouter(tags=["settings"])


def live_settings(db: Session) -> AssistantSettings:
    row = (
        db.query(AssistantSettings)
        .filter_by(status="live")
        .order_by(AssistantSettings.version.desc())
        .first()
    )
    if row is None:
        raise HTTPException(500, "No published assistant settings. Run `make seed`.")
    return row


def draft_settings(db: Session) -> AssistantSettings | None:
    return (
        db.query(AssistantSettings)
        .filter_by(status="draft")
        .order_by(AssistantSettings.version.desc())
        .first()
    )


@router.get("/assistant-settings")
def get_assistant_settings(
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    live = live_settings(db)
    draft = draft_settings(db)
    # **No assembled prompt any more.** The page showed each channel's whole
    # prompt read-only, and that is the rules, the call's and the inbox's
    # instructions and the writing assistant -- the internal half a manager
    # does not edit and should not have to read to change how Liner sounds.
    return {
        "live": settings_out(live),
        "draft": settings_out(draft) if draft else None,
        "has_unpublished_changes": unpublished(live, draft, db),
        "prompt": _prompt_out(db, dealership, live, draft),
    }


#: What a manager can actually change on this page. A draft that matches the
#: live version on every one of these is not an unpublished change, whatever
#: else differs about the rows -- `version`, `status` and the timestamps always
#: do, and comparing whole rows would make the banner permanent.
#:
#: The credit application link is not here: it is never drafted (it goes live
#: on save, through `put_credit_link`), so it can never be an unpublished change.
EDITABLE = (
    "tone", "push_level", "price_mode", "discount_pct", "financing_mode",
    "after_hours_mode", "greeting", "booking_slot_length",
)


def unpublished(live, draft, db: Session | None = None) -> bool:
    """Does the draft actually say something different from what is live?

    **Not `draft is not None`.** That is what it was, and a draft row exists
    from the moment the dealership is seeded -- so every install opened Liner
    setup under a banner announcing changes nobody had made, on an instance
    nobody had touched. A warning that turns out to be wrong is worse than no
    warning: the next one gets ignored too, and this is the banner that stands
    between an edit and a buyer reading it.
    """
    if draft is None:
        return False
    if any(getattr(live, f, None) != getattr(draft, f, None) for f in EDITABLE):
        return True
    # The prompt is drafted and published like every field above, so an edit
    # to it is an unpublished change -- and the one the banner matters most
    # for, since it is what the model is actually told.
    from app.agent.prompts import own_prompt

    return db is not None and own_prompt(db, live) != own_prompt(db, draft)


def _prompt_out(db: Session, dealership: Dealership, live, draft) -> dict:
    """The dealership's one prompt, as the Instructions tab edits it.

    Liner's own is served beside it, unfilled, so the box can start from it
    and Reset can put it back. `""` in `live`/`draft` means that version uses
    Liner's. `room` is the most the box can hold on this store right now --
    the smaller of `max_chars` and what `prompt_max` leaves once every
    channel's internal half is added -- so the page can say "nearly full"
    before a save is refused rather than after.

    **Nothing internal is in here**, by construction rather than by a filter:
    no assembled prompt, no rules, no channel's instructions, no writing
    assistant. The page cannot show what it was never sent.
    """
    from app.agent import prompts

    target = draft or live
    return {
        "default": prompts.default_prompt(),
        "live": prompts.own_prompt(db, live),
        "draft": prompts.own_prompt(db, target),
        "max_chars": prompts.OWN_PROMPT_MAX,
        "prompt_max": prompts.PROMPT_MAX,
        "room": prompts.prompt_room(db, dealership, target),
    }


def _ensure_draft(db: Session) -> AssistantSettings:
    """The draft every edit lands on, made from the live version if missing.

    **The prompt comes with it.** A new draft is minted after every publish,
    and one that copied the fields and not the dealership's own prompt would
    publish Liner's default over it the next time anybody changed the tone --
    a rewrite undone by an unrelated edit, with nothing saying so.
    """
    draft = draft_settings(db)
    if draft is not None:
        return draft
    live = live_settings(db)
    draft = AssistantSettings(
        version=live.version + 1, status="draft", tone=live.tone,
        push_level=live.push_level, price_mode=live.price_mode,
        discount_pct=live.discount_pct, financing_mode=live.financing_mode,
        after_hours_mode=live.after_hours_mode, greeting=live.greeting,
        booking_slot_length=live.booking_slot_length,
        credit_application_url=live.credit_application_url,
    )
    db.add(draft)
    db.flush()
    theirs = db.query(AssistantPrompt).filter_by(settings_id=live.id).one_or_none()
    if theirs is not None:
        # `rules` is not read any more, so it is not carried either.
        db.add(AssistantPrompt(settings_id=draft.id, brief=theirs.brief, rules="",
                               updated_by=theirs.updated_by))
        # Sessions here do not autoflush, so without this the caller's lookup
        # for the draft's prompt misses the row just added and inserts a
        # second one for the same version.
        db.flush()
    return draft


class SettingsPatch(BaseModel):
    tone: str | None = None
    push_level: str | None = None
    price_mode: str | None = None
    discount_pct: int | None = None
    financing_mode: str | None = None
    after_hours_mode: str | None = None
    greeting: str | None = None
    booking_slot_length: int | None = None
    #: Declared only so it can be refused by name. Left off the model it would
    #: be dropped without a word -- a 200 for a link that changed nothing,
    #: which is the one outcome worse than an error.
    credit_application_url: str | None = None


@router.patch("/assistant-settings")
def patch_assistant_settings(
    body: SettingsPatch,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Edits always land on a draft, never on the live row."""
    if body.credit_application_url is not None:
        raise HTTPException(
            400,
            "The credit application link is not drafted -- it goes live when it is "
            "saved. PUT /api/assistant-settings/credit-application-url.",
        )
    draft = _ensure_draft(db)

    for key, value in body.model_dump(exclude_none=True).items():
        setattr(draft, key, value)
    db.commit()
    return {"draft": settings_out(draft)}


#: A finance application collects a social security number, so an address that
#: is not https is not one to send a buyer to -- and this one lands in an email,
#: in a button on the chat and in the `Location` of the counted hop. The same
#: rule the browser enforces, held here because the browser is a request.
CREDIT_LINK = re.compile(r"^https://\S+$", re.IGNORECASE)


class CreditLinkBody(BaseModel):
    url: str = ""


@router.put("/assistant-settings/credit-application-url")
def put_credit_link(
    body: CreditLinkBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> dict:
    """The dealership's finance application, live the moment it is saved.

    **It is not drafted, because it is not behaviour.** Draft and publish exist
    so that a change to how Liner talks is read before a buyer meets it; a
    link to the dealer's own form is a fact about the dealership, and making a
    manager find a Publish button to set it -- after a Save that said it had
    saved -- is asking them to publish a URL. And Save cannot simply publish:
    that would push whatever else is sitting in the draft, a half-rewritten
    brief included, live as a side effect of setting a link.

    **The draft row follows.** Publishing turns the draft row into the live
    one, so a draft left holding the old address would put it back the next
    time anybody published a tone change. `publish_settings` carries the live
    link across as well, so nothing but this endpoint ever changes it.

    **A manager's, like publishing**, because it takes effect at once and it
    is where every buyer who presses the application button is sent.
    """
    url = body.url.strip()
    if url and (not CREDIT_LINK.match(url) or len(url) > 500):
        raise HTTPException(400, "Use the full https:// address of the application page.")
    live = live_settings(db)
    live.credit_application_url = url
    draft = draft_settings(db)
    if draft is not None:
        draft.credit_application_url = url
    db.commit()
    return {"credit_application_url": url, "live": settings_out(live)}


#: The boxes the Instructions tab used to have: a brief, the rules, one per
#: channel and the writing assistant's. Declared so each is refused by name --
#: left off the model, a PUT naming one would be dropped silently and answer
#: 200 for a change that never happened, which is worse than an error.
RETIRED = ("brief", "rules", "chat", "voice", "email", "composer")


class PromptBody(BaseModel):
    """The one prompt. `""` puts Liner's own back."""

    prompt: str | None = None
    brief: str | None = None
    rules: str | None = None
    chat: str | None = None
    voice: str | None = None
    email: str | None = None
    composer: str | None = None


@router.put("/assistant-settings/prompt")
def put_prompt(
    body: PromptBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    """The dealership's own prompt, onto the draft.

    **One text.** How Liner talks to buyers, in the manager's words, used on
    the website chat, on calls and in email replies alike. What each of those
    adds -- no screen on a call, no card in an inbox -- and the writing
    assistant a rep uses are the product's, and a PUT naming one of the old
    boxes is refused by name rather than dropped. Saving Liner's own text
    verbatim, or nothing, stores nothing, so an untouched dealership follows
    the default as it improves rather than freezing a copy of it.

    **A manager's, like publishing.** This is what every conversation starts
    from, so it is not something any rep changes on the way past; and like
    every other field on the page it lands on the draft and reaches nobody
    until it is published.

    **Two ceilings, each said in numbers.** The text itself stays under
    `OWN_PROMPT_MAX`, and then the whole: every channel's assembled prompt
    must stay under `PROMPT_MAX`, measured on the draft as it would be
    published. The second is the one that costs, and on a store with a long
    knowledge table it is the one a manager meets first -- the page shows it
    as `room` before they do.

    **What it cannot change is not in it.** A price the tools did not return,
    a car that is sold, a booking that clashes and a typed field that was a
    guess are refused by executors and guards; the rules that keep Liner
    honest follow the manager's words in code. A rewrite changes how Liner
    talks, never what it may claim.
    """
    from app.agent import prompts

    retired = [key for key in RETIRED if getattr(body, key) is not None]
    if retired:
        raise HTTPException(
            400,
            "Liner has one set of instructions now, sent as `prompt`. How it talks on a "
            "call, writes in an inbox and drafts for your team is part of the product and "
            f"is not edited here ({', '.join(retired)}).",
        )
    if body.prompt is None:
        raise HTTPException(400, "Nothing to save.")
    text = body.prompt.strip()
    if text == prompts.default_prompt():
        # Saving ours stores nothing.
        text = ""
    if len(text) > prompts.OWN_PROMPT_MAX:
        raise HTTPException(
            400,
            f"These instructions are {len(text):,} characters long; the limit is "
            f"{prompts.OWN_PROMPT_MAX:,}. Liner re-reads every word on every message, so "
            "shorter works better.",
        )
    live = live_settings(db)
    unknown = prompts.unknown_placeholders(text, dealership, live)
    if unknown:
        raise HTTPException(
            400,
            "Words in double curly brackets are filled in by Liner, and it does not know "
            + ", ".join("{{" + u + "}}" for u in unknown)
            + ". Write it out in plain words instead.",
        )

    draft = _ensure_draft(db)
    row = db.query(AssistantPrompt).filter_by(settings_id=draft.id).one_or_none()
    if row is None:
        row = AssistantPrompt(settings_id=draft.id)
        db.add(row)
    # `rules` is no longer read; written empty so a row never carries a
    # second text that looks as though it still matters.
    row.brief, row.rules, row.updated_by = text, "", user.id
    db.flush()

    # The whole prompt, as it would be published. Refused rather than stored,
    # so the draft can never hold something the publish would ship over.
    too_long = {
        channel: length
        for channel, length in prompts.prompt_lengths(db, dealership, draft).items()
        if length > prompts.PROMPT_MAX
    }
    if too_long:
        # Measured before the rollback, which takes a draft minted by this
        # request with it.
        room = prompts.prompt_room(db, dealership, draft)
        db.rollback()
        which = ", ".join(f"{c} ({n:,} characters)" for c, n in too_long.items())
        raise HTTPException(
            400,
            f"That is too long for this dealership: with your facts and knowledge "
            f"answers, the whole prompt would pass {prompts.PROMPT_MAX:,} characters for "
            f"{which}. Keep it to {room:,} characters or fewer.",
        )
    db.commit()
    return _prompt_out(db, dealership, live, draft)


@router.post("/assistant-settings/publish")
def publish_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> dict:
    draft = draft_settings(db)
    if draft is None:
        raise HTTPException(400, "Nothing to publish")
    current = live_settings(db)
    # The link is not drafted (`put_credit_link`), so whatever the draft row
    # says about it is not a decision anybody made here -- the live address
    # carries across rather than a publish quietly changing it.
    draft.credit_application_url = current.credit_application_url
    current.status = "archived"
    draft.status = "live"
    draft.published_by = user.id
    draft.published_at = utcnow()
    db.commit()
    return {"live": settings_out(draft)}


@router.get("/handoff-rules")
def list_handoff_rules(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    rows = db.query(HandoffRule).order_by(HandoffRule.key.asc()).all()
    fired = fired_counts(db)
    return {"rules": [handoff_rule_out(r, fired.get(r.id, 0)) for r in rows]}


class RulePatch(BaseModel):
    enabled: bool | None = None
    threshold_value: int | None = None
    route_target: str | None = None
    notify: str | None = None


@router.patch("/handoff-rules/{rule_id}")
def patch_handoff_rule(
    rule_id: str,
    body: RulePatch,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    rule = db.query(HandoffRule).filter_by(id=rule_id).one_or_none()
    if rule is None:
        raise HTTPException(404, "Rule not found")
    if body.notify is not None and body.notify not in {"email_dashboard", "dashboard"}:
        # SMS is out of scope everywhere (§18.5), so it is not an option here.
        raise HTTPException(400, "notify must be 'email_dashboard' or 'dashboard'")
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(rule, key, value)
    db.commit()
    out = handoff_rule_out(rule, fired_counts(db).get(rule.id, 0))
    if not rule.enabled:
        out["warning"] = (
            "Liner will keep going in those situations instead of stopping and asking "
            "for a person."
        )
    return out


@router.get("/knowledge")
def list_knowledge(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    rows = db.query(KnowledgeEntry).order_by(KnowledgeEntry.topic.asc()).all()
    return {"entries": [knowledge_out(k) for k in rows]}


class KnowledgeBody(BaseModel):
    topic: str
    answer: str


@router.post("/knowledge")
def create_knowledge(
    body: KnowledgeBody, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    entry = KnowledgeEntry(topic=body.topic.strip(), answer=body.answer.strip())
    db.add(entry)
    db.commit()
    return knowledge_out(entry)


@router.patch("/knowledge/{entry_id}")
def patch_knowledge(
    entry_id: str,
    body: KnowledgeBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    entry = db.query(KnowledgeEntry).filter_by(id=entry_id).one_or_none()
    if entry is None:
        raise HTTPException(404, "Entry not found")
    entry.topic = body.topic.strip()
    entry.answer = body.answer.strip()
    db.commit()
    return knowledge_out(entry)


@router.get("/rails")
def list_rails(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    from app.agent.rail_actions import retired

    rows = (
        db.query(Rail)
        .order_by(Rail.kind.asc(), Rail.stage.asc(), Rail.sort_order.asc(), Rail.label.asc(),
                  Rail.id.asc())
        .all()
    )
    # A withdrawn chip is not listed as one a manager could switch back on:
    # nothing would offer it, so the toggle would be a control for nothing.
    return {"rails": [rail_out(r) for r in rows if not retired(r)]}


class RailPatch(BaseModel):
    label: str | None = None
    message_text: str | None = None
    enabled: bool | None = None
    sort_order: int | None = None


@router.patch("/rails/{rail_id}")
def patch_rail(
    rail_id: str,
    body: RailPatch,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    rail = db.query(Rail).filter_by(id=rail_id).one_or_none()
    if rail is None:
        raise HTTPException(404, "Rail not found")
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(rail, key, value)
    if not (rail.message_text or "").strip():
        raise HTTPException(400, "A rail with no message text would send an empty turn")
    db.commit()
    return rail_out(rail)
