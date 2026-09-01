"""Liner setup: behaviour, handoff rules, knowledge, rails, compiled prompt.

Draft vs live is the point. An edit is a draft until it is published, otherwise
a tweak silently changes buyer-facing behaviour mid-conversation (§18.2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user, get_dealership, require_manager
from app.db import get_db, utcnow
from app.models import AssistantSettings, Dealership, HandoffRule, KnowledgeEntry, Rail, User
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
    from app.agent.prompts import build_system_prompt

    live = live_settings(db)
    draft = draft_settings(db)
    return {
        "live": settings_out(live),
        "draft": settings_out(draft) if draft else None,
        "has_unpublished_changes": unpublished(live, draft),
        # Read-only. "Here is literally what it was told" is a strong answer to
        # the control objection, and it costs nothing because we assemble this
        # string anyway (§18.3).
        "compiled_prompt": build_system_prompt(db, dealership, live),
    }


#: What a manager can actually change on this page. A draft that matches the
#: live version on every one of these is not an unpublished change, whatever
#: else differs about the rows -- `version`, `status` and the timestamps always
#: do, and comparing whole rows would make the banner permanent.
EDITABLE = (
    "tone", "push_level", "price_mode", "discount_pct", "financing_mode",
    "after_hours_mode", "greeting", "booking_slot_length",
    "credit_application_url",
)


def unpublished(live, draft) -> bool:
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
    return any(getattr(live, f, None) != getattr(draft, f, None) for f in EDITABLE)


class SettingsPatch(BaseModel):
    tone: str | None = None
    push_level: str | None = None
    price_mode: str | None = None
    discount_pct: int | None = None
    financing_mode: str | None = None
    after_hours_mode: str | None = None
    greeting: str | None = None
    booking_slot_length: int | None = None
    credit_application_url: str | None = None


@router.patch("/assistant-settings")
def patch_assistant_settings(
    body: SettingsPatch,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Edits always land on a draft, never on the live row."""
    draft = draft_settings(db)
    if draft is None:
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

    for key, value in body.model_dump(exclude_none=True).items():
        setattr(draft, key, value)
    db.commit()
    return {"draft": settings_out(draft)}


@router.post("/assistant-settings/publish")
def publish_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_manager),
) -> dict:
    draft = draft_settings(db)
    if draft is None:
        raise HTTPException(400, "Nothing to publish")
    current = live_settings(db)
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
    rows = db.query(Rail).order_by(Rail.kind.asc(), Rail.stage.asc(), Rail.sort_order.asc()).all()
    return {"rails": [rail_out(r) for r in rows]}


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
