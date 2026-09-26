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
    #: What it may be set to. Empty means any string, which is what an on/off
    #: switch already was. A flag carrying a *choice* needs this or a typo
    #: silently becomes a fourth state -- `phone_persona="dealerhsip"` reads as
    #: neither persona and the line answers as nobody.
    values: tuple[str, ...] = ()


#: Who picks up Liner's own phone number. Three values, not a boolean, because
#: "nobody" and "somebody, and here is which" is one question and not two -- a
#: separate on/off switch beside a persona choice is two controls that can
#: disagree, and the disagreement is a line that rings out.
PHONE_OFF = "off"
PHONE_LINER = "liner"
PHONE_DEALERSHIP = "dealership"
PHONE_PERSONAS = (PHONE_OFF, PHONE_LINER, PHONE_DEALERSHIP)


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
            values=("off", "on"),
        ),
        Flag(
            key="phone_persona",
            label="Who answers the phone",
            # **Not off, and this one is the exception that proves the rule
            # above it.** Every other flag defaults to the safe side because
            # the permissive side goes wrong quietly. Here the permissive side
            # cannot be reached quietly at all: it takes three secrets in
            # `.env` and a number bought from Twilio and pointed at this host,
            # which is nobody's accident. Given that, defaulting to `off`
            # would mean doing all of that and still getting a line that does
            # not answer, with nothing on any screen saying why -- the exact
            # failure `EMAIL_AGENT` shipped with and had to be fixed for.
            #
            # `dealership` is the switch to throw for a demo: the same number,
            # answered by the buyer-facing assistant, so a prospect can ring it
            # and hear what their own customers would hear.
            default=PHONE_LINER,
            values=PHONE_PERSONAS,
        ),
        Flag(
            key="email_reply_cooldown",
            label="Minutes before Liner answers an email",
            # No default here that means anything -- `""` falls through to
            # `email_agent.cooldown_minutes()`'s own fallback, `.env`'s
            # `EMAIL_REPLY_COOLDOWN_MINUTES`, so a deployment that has never
            # touched this dashboard keeps behaving exactly as it always did.
            # `values=()`: a whole number of minutes is not a closed set the
            # way a persona or an on/off switch is, so the floor (never under
            # one minute -- faster than that reads as a robot, and stops the
            # rep-answers-first window doing its job) is enforced where the
            # value is actually set, in `PATCH /api/email/agent/cooldown`,
            # the same way the credit-application link's `https://` shape is
            # checked at its own endpoint rather than here.
            default="",
        ),
        Flag(
            key="website_chat",
            label="The chat bubble on the dealership's website",
            # **On, and for the reason `phone_persona` is:** the permissive
            # side cannot be reached quietly. The bubble appears only where the
            # dealer's website provider pasted the tag *and* the site is on
            # this dealership's `embed_origins` list -- two deliberate acts by
            # two different people. Defaulting off would mean both of those
            # done and a site with no bubble on it, which reads as "the tag is
            # broken" rather than "a switch is off". This is the half a manager
            # can throw from the dashboard without touching their website: the
            # loader asks on every page load, so off takes effect on the next.
            default="on",
            values=("on", "off"),
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
    allowed = FLAGS[key].values
    if allowed and value not in allowed:
        raise ValueError(
            f"{value!r} is not a value {key} takes. One of: {', '.join(allowed)}."
        )
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
            # What the dashboard may offer. Sent rather than hardcoded in the
            # page for the reason the consent wording is served: a second copy
            # of a closed vocabulary drifts, and the one in the browser is the
            # copy that ends up offering a value the server refuses.
            "values": list(flag.values),
            "reason": row.reason if row is not None else "",
            "updated_at": row.updated_at if row is not None else None,
        })
    return out
