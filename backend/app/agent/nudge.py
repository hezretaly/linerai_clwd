"""One follow-up when a buyer goes quiet mid-conversation.

A buyer who stops typing has usually not left -- they are reading the three
cars they were just shown, or checking something with somebody else in the
room. The window where a second message is welcome rather than pushy is short,
and it closes: an hour later it is an interruption from a shop they had
forgotten about.

**Driven by the buyer's own browser, not by a scheduler.** `/chat` has no
socket and no poll, so a message written into a thread nobody is looking at
would surface only on refresh -- or worse, above their next message, out of
order. Since the only buyer this can help is one still sitting on the page,
the page is what asks: it notices the silence and requests one more turn. A
closed tab produces nothing at all, which is correct -- there is nobody there
to read it. Nothing here is queued, so nothing survives to interrupt somebody
tomorrow.

**Exactly one, and the transcript is what enforces that.** No column and no
counter: a nudge is allowed only when a single assistant message stands after
the buyer's last one -- their reply and nothing else. Once it is sent there are
two, so the next request is refused. The buyer typing resets it naturally,
which is the behaviour wanted: quiet, one prompt, then silence until they say
something. A tab left open all afternoon costs one turn, not one every two
minutes for ever.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Conversation, Message

#: How long a silence has to be. Short enough that the buyer is still on the
#: page and still thinking about the same thing; long enough that it is not
#: answering a pause for breath. The browser holds this clock -- it is here so
#: the server and the page cannot disagree about what "gone quiet" means.
QUIET_SECONDS = 120

#: Appended for this turn only. It is **not** sent as a user message: the
#: buyer said nothing, and a model told otherwise opens with "you're right" to
#: somebody who never spoke -- the exact failure the guard's retry note had to
#: be rewritten for. So it is an instruction about the situation, and it says
#: what the situation is.
NUDGE_ADDENDUM = """
THE BUYER HAS GONE QUIET
They have not answered for a couple of minutes and they are still on the page.
Send one short message, and only one -- you will not get another.

Make it about what they were actually asking. Offer the obvious next step on
it: the car they were looking at, the thing they had not decided, a time to
come in, or a number so somebody can ring them. Do not summarise what has been
said, do not greet them again, do not apologise for the silence, and never ask
"are you still there?" -- it says nothing and answers nothing.

If there is genuinely nothing useful left to add, say one warm line leaving the
door open and stop.
"""


def allowed(db: Session, convo: Conversation) -> tuple[bool, str]:
    """May this conversation be nudged right now, and if not why not.

    Returns a reason rather than a bare False because it is the thing a person
    debugging "why did it not follow up" actually needs, and it goes back on
    the response.
    """
    if convo.status == "closed":
        return False, "The buyer ended this conversation."
    if convo.agent_paused:
        # A rep pressed Take over. They own the thread and their silence is a
        # person deciding what to write, not a gap to fill.
        return False, "A rep has taken this conversation over."

    recent = (
        db.query(Message)
        .filter(Message.conversation_id == convo.id)
        .order_by(Message.created_at.desc())
        .limit(4)
        .all()
    )
    if not recent:
        return False, "Nothing has been said yet."

    since_buyer = []
    for message in recent:
        if message.role == "buyer":
            break
        since_buyer.append(message)

    if not since_buyer:
        # The buyer spoke last and is owed an answer, not a nudge. This is the
        # ordinary turn failing or still running, and prodding it would put a
        # second reply in front of the one on its way.
        return False, "The buyer spoke last; they are owed a reply, not a nudge."
    if any(m.role == "rep" for m in since_buyer):
        return False, "A rep has written to them since."
    if len(since_buyer) > 1:
        # The reply, plus a nudge already. One is the whole allowance.
        return False, "They have already been followed up once."
    return True, ""
