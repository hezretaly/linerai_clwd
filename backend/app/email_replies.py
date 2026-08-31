"""The one background loop in this process: replies that are now due.

Every email reply waits before it goes out -- see `app/email_reply.schedule`.
Something has to notice when the wait is over, and this is it.

**A row with a due time, not a sleeping task.** `asyncio.sleep(3600)` inside a
request handler is lost the moment anything restarts, and this restarts on
every deploy; a row survives one, and the drain simply picks up whatever the
clock has passed. It is also why a reply can be *cancelled* by a rep answering
first, which is most of the point of waiting at all.

**In-process, like `app/events.py`, and for the same reason:** one worker is
already required, and two would drain the same queue twice. The claim below is
what makes that a warning rather than a duplicate email.

It does nothing at all unless `EMAIL_AGENT` is set, so an ordinary deployment
pays one cheap indexed query per tick and no more.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.db import SessionLocal, utcnow

log = logging.getLogger("liner.email")

#: How often to look. Coarse on purpose: the wait is measured in minutes, and a
#: reply going out thirty seconds late is not a thing anyone can perceive --
#: while a tighter tick is a query a second for the rest of the process's life.
TICK_SECONDS = 30


def drain(*, provider=None) -> list[dict]:
    """Answer everything the clock has passed. Returns what happened to each.

    Its own session per pass, and every brake re-run inside `send_due`: the
    wait exists so a person can get there first, and a decision taken when the
    message arrived would defeat it.
    """
    from app import email_reply

    done: list[dict] = []
    with SessionLocal() as db:
        for row in email_reply.due_now(db):
            # Claimed before it is answered, not after. Two processes draining
            # this queue is a misconfiguration -- the event bus already
            # requires one worker -- but the failure it produces is a buyer
            # getting the same reply twice, which is the one worth spending a
            # write to prevent.
            row.state = "sending"
            db.commit()
            try:
                done.append({"id": row.id, **email_reply.send_due(db, row, provider=provider)})
            except Exception as exc:  # a bad reply must not stop the queue
                row.state = "failed"
                row.detail = str(exc)[:500]
                row.resolved_at = utcnow()
                db.commit()
                log.exception("email reply %s failed", row.id)
                done.append({"id": row.id, "sent": False, "reason": "error"})
    return done


async def tick_forever() -> None:
    """Wake, drain, sleep. Cancelled with the process.

    Every exception is caught and logged rather than allowed to end the loop:
    a background task that dies silently leaves a queue that fills up and a
    mailbox that has quietly stopped answering, which looks exactly like the
    agent being switched off.
    """
    while True:
        try:
            if settings.email_agent:
                # In a thread: `drain` is synchronous SQLAlchemy, and running
                # it on the event loop would block every socket in the process
                # for the length of a model round trip.
                answered = await asyncio.to_thread(drain)
                if answered:
                    log.info("email replies due: %s", answered)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("email reply tick failed")
        await asyncio.sleep(TICK_SECONDS)
