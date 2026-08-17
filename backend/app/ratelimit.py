"""A sliding window over recent attempts, in memory.

Only the login form uses it, and only that is worth protecting this way: it is
the one unauthenticated endpoint where guessing repeatedly gets you something.
`/api/inbound-email` has an HMAC in front of it and the demo form writes a row
somebody has to read, which is annoying rather than dangerous.

**In process, like `events.py`.** This system already requires a single uvicorn
worker for the event bus, so a dict is exactly as correct as Redis would be and
has no operational cost. If it ever runs more than one worker, both files move
together -- and until then a second store would be a second thing to run for
no protection this does not already give.

**Keyed on the account, not the caller's address.** The attack worth stopping
is somebody guessing one password until it works, and that is per account
whichever host it comes from. Keying on IP instead has a failure mode this
deployment would actually hit: behind nginx or Cloudflare, every request
carries the proxy's address unless `--proxy-headers` is set, so one bot would
lock out every real person at once. An account key cannot do that -- the worst
a spray achieves is locking the accounts it is spraying, which is the point.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

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
