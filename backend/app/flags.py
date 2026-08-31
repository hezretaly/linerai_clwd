"""Switches somebody can throw without a restart.

Deliberately a closed vocabulary. `FLAGS` below is the whole of it and an
unknown key is refused: a free key/value store reachable from a dashboard
becomes a place to hide configuration that nobody can find again, and this
table exists for exactly one kind of thing -- a control you reach for while
something is going wrong.

Nothing here caches. A kill switch read from memory is one that keeps letting
mail out for as long as the process has been up, which is the one moment it
must not.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import RuntimeFlag


@dataclass(frozen=True)
class Flag:
    key: str
    #: What it does, in the words the dashboard shows.
    label: str
    #: The value when nothing has been set. Safe, always: a flag that defaults
    #: to the permissive side is one that goes wrong quietly.
    default: str


#: Every switch there is.
FLAGS = {
    flag.key: flag
    for flag in (
        Flag(
            key="email_agent",
            label="Liner answers email",
            # Off. Turning it on is a decision a dealership makes, the same
            # shape as VOICE_PROVIDER -- and this is the half that can be
            # thrown back off in a hurry. `EMAIL_AGENT` in `.env` is the
            # other half, and the stricter of the two wins.
            default="off",
        ),
    )
}


def get(db: Session, key: str) -> str:
    if key not in FLAGS:
        raise KeyError(f"{key} is not a runtime flag. Known: {', '.join(sorted(FLAGS))}")
    row = db.query(RuntimeFlag).filter_by(key=key).one_or_none()
    return row.value if row is not None and row.value else FLAGS[key].default


def set(db: Session, key: str, value: str, *, reason: str = "", by: str | None = None) -> str:
    """Throw a switch, and record why.

    `reason` matters more than it looks: the hourly ceiling trips this on its
    own, and without a note the morning after reads as somebody having turned
    it off by hand.
    """
    if key not in FLAGS:
        raise KeyError(f"{key} is not a runtime flag. Known: {', '.join(sorted(FLAGS))}")
    row = db.query(RuntimeFlag).filter_by(key=key).one_or_none()
    if row is None:
        row = RuntimeFlag(key=key)
        db.add(row)
    row.value = value
    row.reason = reason
    row.set_by_user_id = by
    db.commit()
    return row.value


def all_flags(db: Session) -> list[dict]:
    """Every switch and its state, for the dashboard."""
    rows = {row.key: row for row in db.query(RuntimeFlag).all()}
    out = []
    for flag in FLAGS.values():
        row = rows.get(flag.key)
        out.append({
            "key": flag.key,
            "label": flag.label,
            "value": row.value if row is not None and row.value else flag.default,
            "default": flag.default,
            "reason": row.reason if row is not None else "",
            "updated_at": row.updated_at if row is not None else None,
        })
    return out
