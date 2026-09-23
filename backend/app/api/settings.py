"""Liner setup: behaviour, handoff rules, knowledge, rails, compiled prompt.

Draft vs live is the point. An edit is a draft until it is published, otherwise
a tweak silently changes buyer-facing behaviour mid-conversation (§18.2).

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
from app.models import (
    AssistantPart,
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
    from app.agent.prompts import build_system_prompt, composer_system

    live = live_settings(db)
    draft = draft_settings(db)
    return {
        "live": settings_out(live),
        "draft": settings_out(draft) if draft else None,
        "has_unpublished_changes": unpublished(live, draft, db),
        "prompt": _prompt_out(db, live, draft),
        # Read-only. "Here is literally what it was told" is a strong answer to
        # the control objection, and it costs nothing because we assemble this
        # string anyway (§18.3). One per assistant now, because each is told
        # something different on top of the shared brief.
        "compiled_prompt": build_system_prompt(db, dealership, live),
        "compiled": {
            "chat": build_system_prompt(db, dealership, live, "chat"),
            "voice": build_system_prompt(db, dealership, live, "voice"),
            "email": build_system_prompt(db, dealership, live, "email"),
            # The writing assistant's instructions; each draft's facts -- the
            # buyer, the car, the knowledge table -- are composed after them
            # per draft (`email_draft.brief`), so there is no one string.
            "composer": composer_system(db, dealership, live),
        },
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
    # The wording is drafted and published like every field above, so an edit
    # to it is an unpublished change -- and the one the banner matters most
    # for, since it is what the model is actually told.
    from app.agent.prompts import own_prompt

    return db is not None and own_prompt(db, live) != own_prompt(db, draft)


def _prompt_out(db: Session, live, draft) -> dict:
    """The assistant's wording, as the setup page's Advanced tab edits it.

    The product's own text is served beside the dealership's, unfilled, so
    the box can start from it and "Reset to default" can say what it resets
    to. `""` in `draft`/`live` means that version uses the default.
    """
    from app import profile
    from app.agent import prompts

    return {
        "defaults": {
            "brief": prompts.METHOD if profile.assistant()["sales_method"] else prompts.BRIEF,
            "rules": prompts.OPERATING_RULES,
            **{part: prompts.default_part(part) for part in prompts.PARTS},
        },
        "live": prompts.own_prompt(db, live),
        "draft": prompts.own_prompt(db, draft) if draft else prompts.own_prompt(db, live),
        "max_chars": prompts.OWN_PROMPT_MAX,
        "part_max": prompts.PART_MAX,
        "prompt_max": prompts.PROMPT_MAX,
    }


def _ensure_draft(db: Session) -> AssistantSettings:
    """The draft every edit lands on, made from the live version if missing.

    **The wording comes with it.** A new draft is minted after every publish,
    and one that copied the fields and not the dealership's own brief would
    publish the product default over it the next time anybody changed the
    tone -- a rewrite undone by an unrelated edit, with nothing saying so.
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
        db.add(AssistantPrompt(settings_id=draft.id, brief=theirs.brief, rules=theirs.rules,
                               updated_by=theirs.updated_by))
        # Sessions here do not autoflush, so without this the caller's lookup
        # for the draft's wording misses the row just added and inserts a
        # second one for the same version.
        db.flush()
    # Each assistant's own wording travels with it, for the same reason.
    for part in db.query(AssistantPart).filter_by(settings_id=live.id).all():
        db.add(AssistantPart(settings_id=draft.id, part=part.part, text=part.text,
                             updated_by=part.updated_by))
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


class PromptBody(BaseModel):
    """Any of the six, and only what is sent changes. `""` restores ours."""

    brief: str | None = None
    rules: str | None = None
    chat: str | None = None
    voice: str | None = None
    email: str | None = None
    composer: str | None = None


#: What each part is called in a sentence a manager reads.
PART_NAMES = {
    "brief": "the brief", "rules": "the rules", "chat": "the website chat's",
    "voice": "the phone calls'", "email": "the email replies'",
    "composer": "the writing assistant's",
}


@router.put("/assistant-settings/prompt")
def put_prompt(
    body: PromptBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
    dealership: Dealership = Depends(get_dealership),
) -> dict:
    """The dealership's own wording for any assistant, onto the draft.

    **Six parts, one endpoint.** The brief and the rules every buyer-facing
    assistant shares, then one set of instructions each for the website chat,
    the phone line, the email replies and the writing assistant a rep uses.
    Each replaces the product's own text for that part, and saving the default
    verbatim -- or nothing -- stores nothing, so an untouched box follows the
    product's text as it improves rather than freezing a copy of it.

    **A manager's, like publishing.** This is what every conversation starts
    from, so it is not something any rep changes on the way past; and like
    every other field on the page it lands on the draft and reaches nobody
    until it is published.

    **Three ceilings, each said in numbers.** The brief and rules together
    stay under `OWN_PROMPT_MAX`, each part under its own `PART_MAX` (a call's
    is the gate's 1,500, because it is re-read every turn of a call billed by
    the minute), and then the whole: every assistant's assembled prompt must
    stay under `PROMPT_MAX`, measured on the draft as it would be published.
    The first two are about one box; the third is the one that actually costs.

    **What it cannot change is written down on the page too.** A price the
    tools did not return, a car that is sold, a booking that clashes and a
    typed field that was a guess are all refused by executors and guards, not
    by this text -- so a rewrite changes how Liner talks and never what it may
    claim.
    """
    from app.agent import prompts

    given = body.model_dump(exclude_none=True)
    if not given:
        raise HTTPException(400, "Nothing to save.")
    defaults = {"brief": prompts.BRIEF, "rules": prompts.OPERATING_RULES}
    cleaned: dict[str, str] = {}
    for key, value in given.items():
        text = value.strip()
        default = defaults.get(key) or prompts.default_part(key)
        # Saving the default verbatim stores nothing.
        cleaned[key] = "" if text == default.strip() else text

    live = live_settings(db)
    draft = _ensure_draft(db)
    current = prompts.own_prompt(db, draft)
    after = {**current, **cleaned}

    if len(after["brief"]) + len(after["rules"]) > prompts.OWN_PROMPT_MAX:
        raise HTTPException(
            400,
            f"The brief and rules come to {len(after['brief']) + len(after['rules']):,} "
            f"characters; the limit is {prompts.OWN_PROMPT_MAX:,}. Every character is "
            "re-read on every turn of every conversation.",
        )
    for part in prompts.PARTS:
        if len(after[part]) > prompts.PART_MAX[part]:
            raise HTTPException(
                400,
                f"{PART_NAMES[part].capitalize()} instructions come to {len(after[part]):,} "
                f"characters; the limit is {prompts.PART_MAX[part]:,}.",
            )
    unknown = prompts.unknown_placeholders("\n".join(cleaned.values()), dealership, live)
    if unknown:
        raise HTTPException(
            400,
            "These are not placeholders Liner can fill, so they would reach the model "
            "in braces: " + ", ".join("{{" + u + "}}" for u in unknown),
        )

    if "brief" in cleaned or "rules" in cleaned:
        row = db.query(AssistantPrompt).filter_by(settings_id=draft.id).one_or_none()
        if row is None:
            row = AssistantPrompt(settings_id=draft.id)
            db.add(row)
        row.brief, row.rules, row.updated_by = after["brief"], after["rules"], user.id
    for part in prompts.PARTS:
        if part not in cleaned:
            continue
        row = db.query(AssistantPart).filter_by(settings_id=draft.id, part=part).one_or_none()
        if row is None:
            row = AssistantPart(settings_id=draft.id, part=part)
            db.add(row)
        row.text, row.updated_by = cleaned[part], user.id
    db.flush()

    # The whole prompt, as it would be published. Refused rather than stored,
    # so the draft can never hold something the publish would ship over.
    too_long = {
        channel: length
        for channel, length in prompts.prompt_lengths(db, dealership, draft).items()
        if length > prompts.PROMPT_MAX
    }
    if too_long:
        db.rollback()
        which = ", ".join(f"{c} ({n:,} characters)" for c, n in too_long.items())
        raise HTTPException(
            400,
            f"That would take the whole prompt past {prompts.PROMPT_MAX:,} characters "
            f"for: {which}. Every character is re-read on every turn.",
        )
    db.commit()
    return _prompt_out(db, live, draft)


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
    return {"rules": [handoff_rule_out(r) for r in rows]}


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
    out = handoff_rule_out(rule)
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

    rows = db.query(Rail).order_by(Rail.kind.asc(), Rail.stage.asc(), Rail.sort_order.asc()).all()
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
