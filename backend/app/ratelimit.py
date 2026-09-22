"""A sliding window over recent attempts, in memory, and every limit this app
actually runs.

Two unauthenticated surfaces are worth protecting this way and they are
protected against different things:

  - **The login form**, where guessing repeatedly gets you something. That one
    is about *access*.
  - **`/chat`**, where every turn is a model call on somebody's key. That one
    is about *cost*, and it became urgent when the chat became an iframe a
    dealership puts on its own public website: a page any stranger can load
    and any script can post to.

`/api/inbound-email` has an HMAC in front of it and the demo form writes a row
somebody has to read, which is annoying rather than dangerous.

**In process, like `events.py`.** This system already requires a single uvicorn
worker for the event bus, so a dict is exactly as correct as Redis would be and
has no operational cost. If it ever runs more than one worker, both files move
together -- and until then a second store would be a second thing to run for
no protection this does not already give.

**Never keyed on the caller's address**, on either surface. Behind nginx or
Cloudflare every request carries the *proxy's* address unless `--proxy-headers`
is set, so an IP key would refuse every real person at once the moment one
script ran -- a worse outage than the abuse. Login keys on the account, which
is what the attack is per; chat keys on the conversation and on the store,
which is what the *bill* is per.

**The windows live here rather than beside their endpoints**, so "what does
this deployment refuse, and after how many" is one file rather than a search.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

from fastapi import HTTPException, status

#: A ceiling on how many distinct keys are tracked. A spray with a fresh
#: address every attempt would otherwise grow this without limit; the oldest
#: are dropped, which costs an attacker nothing they did not already have.
MAX_KEYS = 4096


class SlidingWindow:
    """`allow()` per key, with the oldest attempts ageing out of the window."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: OrderedDict[str, list[float]] = OrderedDict()
        # Endpoints run in a threadpool, so two attempts on one account can be
        # counted at the same moment. Without this, the check and the append
        # interleave and the limit is a suggestion.
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        for key in list(self._hits):
            fresh = [t for t in self._hits[key] if t > cutoff]
            if fresh:
                self._hits[key] = fresh
            else:
                del self._hits[key]
        while len(self._hits) > MAX_KEYS:
            self._hits.popitem(last=False)

    def retry_after(self, key: str) -> int:
        """Seconds until this key may try again, or 0 when it may now.

        Asked *before* the attempt is recorded, so a caller can refuse without
        counting the refusal -- otherwise a client that keeps retrying while
        blocked pushes its own unlock further away forever.
        """
        now = time.time()
        with self._lock:
            self._prune(now)
            hits = self._hits.get(key, [])
            if len(hits) < self.limit:
                return 0
            return max(1, int(hits[0] + self.window - now) + 1)

    def record(self, key: str) -> None:
        """Count one failure."""
        now = time.time()
        with self._lock:
            self._prune(now)
            self._hits.setdefault(key, []).append(now)
            self._hits.move_to_end(key)

    def clear(self, key: str) -> None:
        """Forget this key -- what a correct password does.

        Without it, somebody who mistyped four times and then got it right
        would still be four attempts from a lockout for the rest of the window.
        """
        with self._lock:
            self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# --- The chat's ceilings ----------------------------------------------------
# Built from settings at import, like the login window in `api/auth.py`. See
# `config.chat_window_seconds` for why there are three and not one.
#
# Imported lazily inside the functions below rather than at module scope:
# `config` imports nothing from here, but keeping the direction of the arrow
# obvious is worth one import statement.

def _windows() -> tuple[SlidingWindow, SlidingWindow, SlidingWindow]:
    global _NEW_SESSIONS, _TURNS_PER_CONVERSATION, _TURNS_PER_STORE
    if _NEW_SESSIONS is None:
        from app.config import settings

        window = settings.chat_window_seconds
        _NEW_SESSIONS = SlidingWindow(settings.chat_max_sessions_per_store, window)
        _TURNS_PER_CONVERSATION = SlidingWindow(
            settings.chat_max_turns_per_conversation, window
        )
        _TURNS_PER_STORE = SlidingWindow(settings.chat_max_turns_per_store, window)
    return _NEW_SESSIONS, _TURNS_PER_CONVERSATION, _TURNS_PER_STORE


_NEW_SESSIONS: SlidingWindow | None = None
_TURNS_PER_CONVERSATION: SlidingWindow | None = None
_TURNS_PER_STORE: SlidingWindow | None = None


def _refuse(wait: int, what: str) -> None:
    """429 with `Retry-After`, in the shape the login form already uses.

    The wording is the buyer's, not ours. They did nothing wrong and cannot
    act on "rate limit exceeded" -- what they can act on is that it is
    temporary and that a person can still be reached.
    """
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"{what} Please try again in {wait} seconds, or call the dealership.",
        headers={"Retry-After": str(wait)},
    )


def new_conversation(store: str) -> None:
    """One more chat session in this store, or a 429.

    Keyed on the store because this is the abuse a per conversation limit is
    blind to by construction: a script that mints a fresh conversation per
    message has a fresh key every time, so only a ceiling *above* the
    conversation can see it at all.
    """
    sessions, _, _ = _windows()
    key = store or "-"
    wait = sessions.retry_after(key)
    if wait:
        _refuse(wait, "The chat is busy right now.")
    sessions.record(key)


def turn_wait(store: str, conversation_id: str) -> int:
    """Seconds until this conversation may take another model turn, or 0.

    Both ceilings, asked before either is counted -- otherwise a request
    refused by the second still spends a slot in the first, and a caller who
    keeps retrying pushes their own unlock further away. Same reasoning as
    `retry_after` being asked before `record`.

    Asking and counting are separate calls because the two endpoints that
    spend a turn refuse differently: `/messages` owes the buyer a 429, and
    `/nudge` answers 200 with a reason, since nothing has gone wrong when a
    follow-up nobody asked for does not happen.
    """
    _, per_convo, per_store = _windows()
    return per_convo.retry_after(conversation_id) or per_store.retry_after(store or "-")


def count_turn(store: str, conversation_id: str) -> None:
    """Spend one, against both ceilings."""
    _, per_convo, per_store = _windows()
    per_convo.record(conversation_id)
    per_store.record(store or "-")


def turn(store: str, conversation_id: str) -> None:
    """One more model turn, or a 429. The `/messages` half of the pair."""
    wait = turn_wait(store, conversation_id)
    if wait:
        _refuse(wait, "That is a lot of messages at once.")
    count_turn(store, conversation_id)


