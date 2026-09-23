from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import (
    email_agent,
    email_draft,
    matching,
    outreach_send,
    sms as sms_module,
    timeline,
)
from app.agent import loop
from app.integrations import twilio_account
from app.integrations.registry import get_email_sender
from app.integrations.sms import twilio_sms
from app.recap import lead_recap
from app.api.deps import current_user, find_staff
from app.db import get_db, utcnow
from app.events import emit
from app.models import (
    Appointment,
    CapturedField,
    Conversation,
    Escalation,
    Lead,
    LeadAddress,
    Message,
    Outreach,
    User,
    Vehicle,
)
from app.schemas.serialize import (
    conversation_out, iso, lead_out, stamp, user_out, vehicle_out,
)

router = APIRouter(prefix="/leads", tags=["leads"])


def _get(db: Session, lead_id: str) -> Lead:
    lead = db.query(Lead).filter_by(id=lead_id).one_or_none()
    if lead is None:
        raise HTTPException(404, "Lead not found")
    return lead

# A lead has no stage column -- the state lives on its conversation and its
# appointments. This derives one rather than adding a field that two writers
# would then have to keep in step.
STAGE_RANK = {
    "opening": 0, "browsing": 1, "vehicle_focus": 2, "objection": 2,
    "qualifying": 3, "slot_offered": 3, "contact_capture": 3, "booked": 4,
}


def lead_summaries(db: Session, leads: list[Lead]) -> dict[str, dict]:
    """Per-lead stage, vehicle of interest and last activity, in three queries."""
    ids = [lead.id for lead in leads]
    if not ids:
        return {}

    convos = db.query(Conversation).filter(Conversation.lead_id.in_(ids)).all()
    appts = db.query(Appointment).filter(Appointment.lead_id.in_(ids)).all()
    # Email is contact. `channels` and `last_touch_at` counted conversations
    # only, so a buyer you had exchanged four emails with showed no channel at
    # all and a `last_touch_at` from whenever they last chatted -- which put
    # them at the bottom of a list ordered by activity, on the day they wrote.
    # The buyer page's own channel strip already counted mail; the list did
    # not, and two answers to "when did we last hear from them" is exactly the
    # disagreement this file exists to prevent.
    mail = db.query(Outreach).filter(
        Outreach.lead_id.in_(ids), Outreach.channel == "email"
    ).all()
    convo_ids = [c.id for c in convos]
    last_message = dict(
        db.query(Message.conversation_id, func.max(Message.created_at))
        .filter(Message.conversation_id.in_(convo_ids))
        .group_by(Message.conversation_id)
        .all()
    ) if convo_ids else {}
    open_escalations = {
        row.conversation_id
        for row in db.query(Escalation).filter(Escalation.claimed_at.is_(None)).all()
    }

    vehicle_ids = {c.focus_vehicle_id for c in convos if c.focus_vehicle_id}
    vehicle_ids |= {a.vehicle_id for a in appts if a.vehicle_id}
    vehicles = {
        v.id: v
        for v in (
            db.query(Vehicle).filter(Vehicle.id.in_(vehicle_ids)).all() if vehicle_ids else []
        )
    }

    # An imported lead has no conversation, so the car it asked about lives on a
    # captured field. Matched back to a real row here rather than shown as free
    # text, so the table never names a car that is not on the lot.
    wanted = {
        row.lead_id: row.value
        for row in db.query(CapturedField)
        .filter(CapturedField.lead_id.in_(ids), CapturedField.key == "vehicle_interest")
        .all()
    }
    by_label: dict[str, Vehicle] = {}
    if wanted:
        for v in db.query(Vehicle).filter(Vehicle.status == "available").all():
            by_label[f"{v.year} {v.make} {v.model}".lower()] = v

    out: dict[str, dict] = {}
    for lead in leads:
        mine = [c for c in convos if c.lead_id == lead.id]
        my_appts = [a for a in appts if a.lead_id == lead.id]
        live = [a for a in my_appts if a.status in {"booked", "confirmed"}]

        best = max((STAGE_RANK.get(c.stage, 0) for c in mine), default=0)
        if live:
            stage = "appointment"
        elif best >= 3:
            stage = "qualified"
        elif best >= 1:
            stage = "qualifying"
        else:
            stage = "new"

        vehicle = next(
            (
                vehicles.get(vid)
                for vid in (
                    [a.vehicle_id for a in my_appts] + [c.focus_vehicle_id for c in mine]
                )
                if vid and vehicles.get(vid)
            ),
            None,
        )
        if vehicle is None and lead.id in wanted:
            label = " ".join(wanted[lead.id].split()[:3]).lower()
            vehicle = by_label.get(label)

        # The last thing that actually happened, not when the thread opened. The
        # conversations list is ordered by this, and a chat someone started this
        # morning and abandoned should not outrank one being typed in now.
        my_mail = [o for o in mail if o.lead_id == lead.id]
        touches = [last_message.get(c.id) or c.started_at for c in mine]
        touches += [a.created_at for a in my_appts]
        touches += [o.sent_at or o.created_at for o in my_mail]

        still_open = [c for c in mine if c.status != "closed"]
        out[lead.id] = {
            "stage": stage,
            "flagged": any(c.id in open_escalations for c in mine),
            "vehicle_of_interest": vehicle_out(vehicle) if vehicle else None,
            "appointment_count": len(live),
            "unconfirmed_count": len([a for a in live if a.status == "booked"]),
            "last_touch_at": stamp(max(touches)) if touches else stamp(lead.created_at),
            "conversation_id": mine[0].id if mine else None,
            # What the conversations list needs to draw a lead row without a
            # query per row: how many threads, which channels, and whether any
            # of it is still running.
            "conversation_count": len(mine),
            # Counted, never declared -- the same rule the buyer page's channel
            # strip follows. A lead who has only ever been emailed reads
            # "Email" rather than nothing at all.
            "channels": sorted(
                {c.channel for c in mine} | ({"email"} if my_mail else set())
            ),
            "open": bool(still_open),
            # Declined only while it stays declined. A buyer who said no in
            # March and is chatting again today is not a closed lead.
            "declined": (
                not still_open and any(c.outcome == "declined" for c in mine)
            ),
        }
    return out


@router.get("")
def list_leads(
    source: str | None = Query(None),
    risk: bool | None = Query(None, description="Only leads with no way to reach them"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    query = db.query(Lead)
    if source:
        query = query.filter(Lead.source == source)
    rows = query.order_by(Lead.created_at.desc()).all()
    if risk is True:
        # contact_risk inverted when SMS came out: no email is what makes a
        # lead unreachable now, not a missing phone number (§18.5).
        rows = [lead for lead in rows if lead.contact_risk]

    # The table needs a stage, a vehicle and a last-touch per row. Computing
    # those from a detail call per lead would be N+1; they are gathered here in
    # three queries and folded onto each row.
    summaries = lead_summaries(db, rows)
    leads = []
    for lead in rows:
        out = lead_out(lead, db)
        out.update(summaries.get(lead.id, {}))
        leads.append(out)
    return {"leads": leads}


@router.get("/{lead_id}")
def get_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    lead = _get(db, lead_id)
    out = lead_out(lead, db, detail=True)
    out.update(lead_summaries(db, [lead]).get(lead.id, {}))
    return out


class AssignBody(BaseModel):
    #: Null hands the buyer back to the unclaimed queue.
    user_id: str | None = None


@router.post("/{lead_id}/assign")
def assign_lead(
    lead_id: str,
    body: AssignBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Give a buyer an owner -- and, with them, everything of theirs that was
    waiting for one.

    The overview asks three questions about the same people: who needs a
    person, what is happening now, and who belongs to nobody. Those were three
    unconnected facts, so a rep could assign a lead and still find them sitting
    in Needs a person, and claim an escalation without the buyer leaving the
    unclaimed queue. Two panels disagreeing about whether somebody is being
    looked after is worse than either panel alone.

    So assignment is one act with one definition, and it is here. An open
    escalation on a buyer who now has an owner is claimed by that owner: the
    queue means "waiting for a person to be found", and one has been.

    It does **not** pause Liner. Only a rep pressing Take over does that, which
    is a different decision -- a buyer whose question a human must answer still
    wants the other nine answered meanwhile.

    **Handing a buyer to somebody else is the manager's call; taking one
    yourself is not.** Who works which lead is how a floor is run, and a rep
    quietly moving buyers off a colleague -- or off themselves, back into the
    pool -- is the same act as reassigning them. Taking one over is the
    exception and stays open to everyone, because that is a rep saying "I have
    this", which is the thing the queues are asking for.
    """
    lead = _get(db, lead_id)
    chosen = None
    if body.user_id:
        chosen = find_staff(db, body.user_id)
        if chosen is None:
            raise HTTPException(404, "User not found")

    if user.role != "manager" and (chosen is None or chosen.id != user.id):
        raise HTTPException(
            403,
            "Only a sales manager can give a buyer to somebody else. "
            "You can take this one over yourself.",
        )

    lead.assigned_user_id = chosen.id if chosen else None

    claimed = 0
    if chosen is not None:
        threads = [
            c.id for c in db.query(Conversation.id).filter(Conversation.lead_id == lead.id).all()
        ]
        for escalation in (
            db.query(Escalation)
            .filter(
                Escalation.conversation_id.in_(threads or [""]),
                Escalation.claimed_at.is_(None),
            )
            .all()
        ):
            escalation.claimed_by_user_id = chosen.id
            escalation.claimed_at = utcnow()
            claimed += 1
    # Unassigning deliberately leaves claimed escalations claimed. Somebody
    # really did pick that up, and taking the buyer off them later does not
    # un-happen it -- reopening resolved work because an owner changed is how a
    # queue starts lying in the other direction.

    db.commit()
    emit(db, "lead.assigned", {
        "lead_id": lead.id,
        "user_id": chosen.id if chosen else None,
        "user_name": chosen.name if chosen else "",
        "escalations_claimed": claimed,
    })
    return {**lead_out(lead, db), "escalations_claimed": claimed}


@router.get("/{lead_id}/timeline")
def get_timeline(
    lead_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Every channel this buyer used, in one ordered list.

    `channels` is counted from the entries rather than declared, so the filter
    strip can only offer a channel the buyer actually used -- and can never
    offer one this system cannot do at all.
    """
    lead = _get(db, lead_id)
    entries = timeline.lead_timeline(db, lead)
    convos = (
        db.query(Conversation)
        .filter_by(lead_id=lead.id)
        .order_by(Conversation.started_at.asc())
        .all()
    )
    return {
        "lead": lead_out(lead, db, detail=True),
        "entries": entries,
        "channels": timeline.channel_counts(entries),
        "conversations": [conversation_out(c, db) for c in convos],
        # Lead-level, not the newest thread's: an appointment booked in a
        # chat last night does not belong to the call made this morning.
        "recap": lead_recap(db, lead),
        # The sign-off *this person's* email goes out with -- their own, or
        # their name and title over the dealership's details -- so the
        # composer shows what will actually be appended rather than an
        # impression of it. Served rather than written into the page for the
        # same reason the dealership's name is.
        "email_signature": outreach_send.signature_for(db, user),
        # Where a reply goes. A rep typing into this page has to be told which
        # thread they are answering on -- the alternative is a message landing
        # on a conversation the buyer closed last week.
        "reply_to": _reply_target(convos),
    }


def _reply_target(convos: list[Conversation]) -> str | None:
    """The most recently active thread that is still open, or nothing.

    Nothing is a real answer: Liner cannot start a chat with someone who is not
    on the page. When every thread is closed the page offers the outreach
    composers instead, which is the only way a dealer can actually reach out.
    """
    live = [c for c in convos if c.status != "closed"]
    if not live:
        return None
    return max(live, key=lambda c: c.started_at).id


class AddressBody(BaseModel):
    address: str


@router.post("/{lead_id}/addresses")
def link_address(
    lead_id: str,
    body: AddressBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Say that this buyer also writes from that address.

    `leads.email` is one column and a buyer is not. Somebody who chatted from
    a work address and later mails from a personal one is one person that no
    rule here can see -- matching is email exact and phone by its last ten
    digits, deliberately, because a name is not identity. So the join is a
    human act: a rep who knows says so, and this records that they did.

    Their earlier mail is claimed straight away, through the same ladder the
    live path uses. A link that does not move anything on the timeline is a
    button whose effect nobody can see.

    **An address that already belongs to somebody is refused, never moved.**
    Taking it would silently merge two buyers, which is the one failure
    `app/matching.py` exists to prevent -- and the refusal names who has it, so
    the rep can look rather than being told no.
    """
    lead = _get(db, lead_id)
    address = (body.address or "").strip().lower()
    if "@" not in address or len(address) < 3:
        raise HTTPException(400, "That is not an email address.")
    if address == (lead.email or "").strip().lower():
        raise HTTPException(400, "That is already their address.")

    owner = matching.match_lead(db, address, "", exclude_id=lead.id)
    if owner is not None:
        raise HTTPException(
            409,
            f"{address} already belongs to {owner.name or 'another buyer'}. "
            "Merging two buyers is not something this can do on its own -- "
            "open them side by side and decide.",
        )
    if db.query(LeadAddress).filter_by(lead_id=lead.id, address=address).first():
        raise HTTPException(409, "That address is already linked to them.")

    db.add(LeadAddress(lead_id=lead.id, address=address, added_by_user_id=user.id))
    db.commit()
    claimed = matching.claim_unresolved(db, lead)
    emit(db, "lead.updated", {"lead_id": lead.id, "linked_address": address})
    return {
        "addresses": _addresses_of(db, lead.id),
        # How much of their history moved. Reported because it is the visible
        # consequence, and a rep who links an address and sees nothing change
        # has no way to tell it worked from a mistyped address.
        "claimed": claimed,
    }


@router.delete("/{lead_id}/addresses/{address_id}")
def unlink_address(
    lead_id: str,
    address_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Undo the link. The mail it claimed stays where it was filed.

    Deliberately: a receipt records what happened, and rewriting one to say
    something other than what happened is the one thing a receipt must never
    do. Unlinking stops *future* mail matching, which is what a rep who linked
    the wrong address needs.
    """
    _get(db, lead_id)
    row = db.query(LeadAddress).filter_by(id=address_id, lead_id=lead_id).one_or_none()
    if row is None:
        raise HTTPException(404, "No such linked address.")
    db.delete(row)
    db.commit()
    return {"addresses": _addresses_of(db, lead_id)}


def _addresses_of(db: Session, lead_id: str) -> list[dict]:
    rows = (
        db.query(LeadAddress)
        .filter_by(lead_id=lead_id)
        .order_by(LeadAddress.created_at.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "address": r.address,
            "added_by": user_out(
                db.query(User).filter_by(id=r.added_by_user_id).one_or_none()
                if r.added_by_user_id else None
            ),
            "created_at": stamp(r.created_at),
        }
        for r in rows
    ]


@router.get("/{lead_id}/duplicates")
def get_duplicates(
    lead_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Other leads that look like the same person, and why.

    Detection only -- nothing here merges anything. The reason is returned
    because a rep deciding whether two rows are one person needs to know
    whether we saw the same address or only the same phone; a shared household
    number is a real thing, and "trust us" is not something they can check.

    A name is never a reason. Two Dave Joneses are two people.
    """
    lead = _get(db, lead_id)
    found = matching.candidates_for(db, lead.email, lead.phone, exclude_id=lead.id)
    summaries = lead_summaries(db, [other for other, _ in found])
    out = []
    for other, why in found:
        row = lead_out(other, db)
        row.update(summaries.get(other.id, {}))
        out.append({"reason": why, "lead": row})
    return {"duplicates": out}


class TextBody(BaseModel):
    body: str
    #: Optional. Defaults to the number on the buyer's row, which is what a rep
    #: means by "text them"; passed only to reach a second number they gave.
    to: str = ""


@router.get("/{lead_id}/reach")
def reach(
    lead_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Every way this buyer can be reached, and why not where they cannot.

    **One question, asked once.** The buyer page used to answer it in six
    places in three different wordings -- `lead?.email ?` drew the Email
    button, `if (!lead.email) return null` hid one composer, another drew a To
    line for an empty address, and only the text button consulted whether the
    provider was configured at all. That is the shape `lib/conversationFilters`,
    `app/threads.py` and `app/escalations.py` each exist to prevent: a fact
    written in one row and read from several, which drift.

    Asked before the composer opens rather than discovered on send, because
    "no address on file", "they texted STOP" and "Twilio is not set up" are
    three different answers and only the first is about this buyer.

    **`available` is what may be offered; `delivers` is whether anything
    leaves the building.** They are separate on purpose. With the default
    outbox sender an email is recorded and nothing is sent, and hiding the
    composer for that would make the outbox untestable from the one page a
    rep works from -- so the channel stays offered and the composer says so,
    which is the rule `blocked_reason` already follows by not biting on a
    sender that delivers nothing.
    """
    lead = _get(db, lead_id)
    address = (lead.email or "").strip()
    number = (lead.phone or "").strip()
    sender = get_email_sender()

    texting_on = sms_module.offered()
    drafting = email_agent.have_model()
    sms_blocked = sms_module.blocked_reason(number) if number else ""

    # Every address this buyer is known by, for the composer's To suggestions:
    # the one on their row first, then any a rep has linked. `to` stays the
    # one string it always was; this is the list beside it.
    known = [{"address": address, "label": "On file"}] if address else []
    seen = {address.lower()} if address else set()
    for row in (
        db.query(LeadAddress)
        .filter_by(lead_id=lead.id)
        .order_by(LeadAddress.created_at.asc())
        .all()
    ):
        if row.address and row.address.lower() not in seen:
            seen.add(row.address.lower())
            known.append({"address": row.address, "label": "Also writes from"})

    return {
        "email": {
            "to": address,
            "addresses": known,
            "available": bool(address),
            "reason": "" if address else "No email address on file for this buyer.",
            # Recorded either way; this says whether it also arrives.
            "delivers": bool(getattr(sender, "delivers", False)),
            # Whether **Draft with Liner** can write anything, asked here with
            # the same `have_model` the draft endpoint refuses on. Offering the
            # control and discovering the answer only after it is pressed was
            # a button that did nothing a rep could see -- so the composer is
            # told first, and says why in place of the control.
            "draft": {
                "available": drafting.allowed,
                "reason": "" if drafting.allowed else drafting.detail,
            },
        },
        "sms": {
            "to": number,
            # Three separate facts, deliberately not collapsed into one
            # boolean: the deployment has not switched texting on, this buyer
            # has no number, or this buyer said STOP. One boolean over the
            # three sends a rep to the wrong place -- the same reason
            # `/api/integrations` reports "switched off" and "not configured"
            # as different things.
            "available": bool(number) and texting_on and not sms_blocked,
            "reason": (
                "" if (number and texting_on and not sms_blocked)
                else "No phone number on file for this buyer." if not number
                else "Texting is not set up for this deployment yet."
                if not texting_on else sms_blocked
            ),
            "segment": twilio_sms.SEGMENT,
            "max_body": twilio_sms.MAX_BODY,
        },
        # **A call needs no provider, because nothing here places it.** The
        # Twilio number this system holds is Liner's own -- `/ops/phone` rings
        # prospects from it and `require_owner` guards that -- and a
        # dealership has no outbound line of its own. So "call them" is the
        # rep's own handset: a `tel:` link, available whenever there is a
        # number to dial, and honest about being nothing more than that.
        "call": {
            "to": number,
            "available": bool(number),
            "reason": "" if number else "No phone number on file for this buyer.",
        },
    }


class DraftBody(BaseModel):
    #: The rep's one line: "ask if Saturday works", "answer their financing
    #: question". Optional, because a draft with none is still the obvious
    #: next message -- but it is what makes the feature repeatable rather
    #: than one guess the rep rewrites by hand.
    instruction: str = ""
    #: The rep's own text, when they want it put into the dealership's voice
    #: rather than written from scratch. Their facts are kept.
    rewrite: str = ""
    #: Which thread this is about. Defaults to the buyer's newest.
    conversation_id: str = ""


@router.post("/{lead_id}/draft-email")
def draft_email(
    lead_id: str,
    body: DraftBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Write an email for this rep to read, edit and decide on.

    **Nothing is sent and nothing is stored.** The draft goes back in the
    response and lives in the rep's browser until they press send, which is
    what every other dealership draft here does -- there is no Drafts tab
    because nothing stores a draft, and a model writing one does not change
    that. The send still goes through the composer and still through
    `blocked_reason`.

    **It cannot act.** `loop.draft_text` withholds the tool schema, so the
    model that writes this cannot book the appointment it offers, close the
    thread or raise a handoff. Reusing the buyer loop would have done all
    three as a side effect of drafting -- and, through `may_reply`'s hourly
    ceiling, could have thrown the email kill switch.

    **The brakes that do not apply, and why.** `EMAIL_AGENT`, the runtime
    flag, the cooldown, the per-correspondent gap and the hourly ceiling all
    exist to stop Liner answering a buyer *on its own*. A person asked for
    this and a person decides whether it leaves, so none of them is the
    question here -- and asking `email_agent.enabled` anyway refused every
    draft on a deployment that had simply not turned the autonomous replies
    on, which is the default and the documented state, citing a switch the
    rep had not touched. `have_model` is the one brake that does apply: with
    `LLM_MODE=stub` there is nothing to write with, and the honest answer is
    to say so rather than hand back a template the rep cannot tell from a
    real draft.
    """
    lead = _get(db, lead_id)

    verdict = email_agent.have_model()
    if not verdict.allowed:
        # Typed, and it names the setting. Same shape `/api/email/agent`
        # answers with, because "why did nothing happen" is the question a
        # person actually has.
        raise HTTPException(503, detail={"reason": verdict.reason, "detail": verdict.detail})

    convo = None
    if body.conversation_id:
        convo = (
            db.query(Conversation)
            .filter(Conversation.id == body.conversation_id, Conversation.lead_id == lead.id)
            .one_or_none()
        )
    if convo is None:
        convo = (
            db.query(Conversation)
            .filter(Conversation.lead_id == lead.id)
            .order_by(Conversation.started_at.desc())
            .first()
        )
    if convo is None:
        raise HTTPException(
            409,
            "This buyer has no conversation yet, so there is nothing to draft from. "
            "Write the first message yourself.",
        )

    text, violations = loop.draft_text(
        db,
        convo,
        brief=email_draft.brief(
            db, lead, convo, instruction=body.instruction, rewrite=body.rewrite,
            # Written as the person pressing the button: it goes out under
            # their name and their sign-off.
            author=user,
        ),
    )
    subject, text = email_draft.split_subject(text)
    return {
        # Empty when the model gave none; the composer then leaves the rep's
        # subject box alone rather than blanking it.
        "subject": subject,
        "body": text,
        # Shown to the rep rather than swallowed. A draft the guards refused
        # twice is one carrying a claim nothing sourced, and the rep is the
        # person who can decide whether they know it to be true.
        "violations": violations,
        "conversation_id": convo.id,
    }


@router.post("/{lead_id}/sms")
def send_sms(
    lead_id: str,
    body: TextBody,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """A rep texts this buyer.

    **A person wrote this and a person pressed send.** No assistant is involved
    on this path and there is no setting that would put one there; see
    `app/sms.py`.

    Open to any rep, like the email composer: answering a buyer is the work,
    not an administrative act. What it will not skip is `blocked_reason` --
    a composer is exactly where a rehearsal reaches a real prospect, and here
    it reaches their phone.
    """
    lead = _get(db, lead_id)
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(400, "There is no message to send.")

    to = (body.to or lead.phone or "").strip()
    if not to:
        raise HTTPException(400, "No number on file for this buyer.")

    base = twilio_account.base_url(str(request.base_url))
    row = sms_module.send(
        db, to, text, lead=lead, by=user,
        status_url=twilio_account.webhook_url("/api/phone/sms-status", base) if base else "",
    )
    return {
        "sent": row.status not in ("failed",),
        "status": row.status,
        # The provider's own words, verbatim, when it refused. A composer that
        # says only "failed" costs whoever reads it a search through the logs.
        "detail": row.error,
        "outreach_id": row.id,
    }
