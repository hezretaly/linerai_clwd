#!/usr/bin/env python3
"""Why a message sent to one of our addresses did not arrive.

    make mail-check TO=alsbou@linerai.us

**Sending breaks loudly and receiving breaks silently.** A send quotes the
provider's own error on the next attempt; a delivery that never arrives looks
exactly like a buyer who did not write, and the reason can be any of four
things, three of which are outside this application:

  1. Cloudflare never called the Worker -- no MX, or no route for that address.
  2. The Worker refused the recipient. Its filter runs *before* it posts, so a
     dropped message leaves no receipt, no row and no error: the `founder@`
     failure, one dealership at a time.
  3. The backend refused it -- a wrong `WEBHOOK_SECRET`, or a duplicate.
  4. It was filed correctly, in a store whose dashboard nobody was looking at.

Only the last two leave a trace here, and that is the point of this script:
it says which of the four you are in, and it never guesses. What it cannot see
it says it cannot see -- the deployed Worker's recipient list is at Cloudflare
and a local file is not evidence about it.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))

from app import mailboxes  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import has_database, SessionLocal  # noqa: E402
from app.email_intake import is_ours  # noqa: E402
from app.models import InboundEmail  # noqa: E402
from app.stores import known_stores  # noqa: E402

WORKER = (
    pathlib.Path(__file__).resolve().parent.parent
    / "backend/app/integrations/email/worker/wrangler.jsonc"
)


def recipients() -> list[str]:
    """The Worker's recipient filter as this checkout has it.

    Read out of `wrangler.jsonc` rather than restated, so it cannot drift from
    what a deploy would install -- and reported as what it is: the list in the
    repository, which only matches Cloudflare after `wrangler deploy`.
    """
    if not WORKER.is_file():
        return []
    for line in WORKER.read_text().splitlines():
        if "ALLOWED_RECIPIENTS" in line:
            _, _, rest = line.partition(":")
            return [p.strip() for p in rest.strip().strip('",').split(",") if p.strip()]
    return []


def receipts(address: str) -> list[tuple[str, InboundEmail]]:
    """Every delivery recorded for this address, across every store and ops.

    Walked rather than looked up in one place: the receipt is written into the
    store the envelope routed to, so asking only the default store is how a
    dealership's mail reads as missing when it is filed one file over. Seeded
    stores only -- opening an unseeded one would create it.
    """
    found: list[tuple[str, InboundEmail]] = []
    for slug in [""] + [s for s in known_stores() if s != settings.dealership and has_database(s)]:
        with SessionLocal(slug) as db:
            rows = (
                db.query(InboundEmail)
                .filter(InboundEmail.to_address.ilike(f"%{address}%"))
                .order_by(InboundEmail.created_at.desc())
                .limit(10)
                .all()
            )
            found += [(slug or f"{settings.dealership or 'default'} (unprefixed)", r) for r in rows]
    return found


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(__doc__)
        print("Usage: make mail-check TO=alsbou@linerai.us")
        return 2
    address = sys.argv[1].strip().lower()
    local = address.partition("@")[0]

    print(f"\n== {address}\n")

    # 1. Whose is it, and is that store even here?
    if is_ours(address):
        print("  realm    Liner's own -- this one is read at /ops, not on a dealership's")
        print("           dashboard. A manager will never see it, by design.")
        slug = ""
    else:
        slug = mailboxes.store_for(address)
        boxes = mailboxes.mailboxes()
        if slug:
            print(f"  store    {slug} -- its profile declares `mailbox: {local}`")
            print(f"  read at  /{slug}/app/campaigns, and on the buyer's own page")
        elif local in {"reply", *[b for b in boxes]}:
            print(f"  store    a reply token, looked up across every seeded store")
        else:
            print("  store    no profile declares this mailbox, so it stays with the")
            print(f"           default store ({settings.dealership or 'unprefixed'})")
            print(f"           declared mailboxes: {json.dumps(boxes)}")
    if slug and not has_database(slug):
        print(f"  WARNING  {slug} has no database on this host, so nothing can be filed")
        print(f"           into it. Run: DEALERSHIP={slug} make reset-db")

    # 2. What the Worker in this checkout would keep. Not what Cloudflare has.
    allowed = recipients()
    listed = any(address.startswith(a.rstrip("@")) or a.rstrip("@") == local for a in allowed)
    print()
    if not allowed:
        print("  worker   could not read ALLOWED_RECIPIENTS out of wrangler.jsonc")
    elif listed:
        print(f"  worker   this checkout lists `{local}@` in ALLOWED_RECIPIENTS")
        print("           -- which is true at Cloudflare only after `wrangler deploy`.")
        print("           Unverifiable from here: the deployed list is not readable")
        print("           without Cloudflare credentials, which this box does not have.")
    else:
        print(f"  worker   `{local}@` is NOT in this checkout's ALLOWED_RECIPIENTS.")
        print(f"           Cloudflare drops it before posting, leaving no receipt at")
        print(f"           all. Add it to wrangler.jsonc and `wrangler deploy`.")
        print(f"           listed: {', '.join(allowed)}")

    # 3. The receipts, which are the only hard evidence on this host.
    rows = receipts(address)
    print()
    if not rows:
        print("  receipts none. Nothing has ever been POSTed to this deployment for")
        print("           that address -- so it did not get past Cloudflare or the")
        print("           Worker, and the answer is upstream of this app:")
        print("             - Email Routing on, MX pointing at Cloudflare")
        print("             - a catch-all route (or a rule) to this Worker")
        print("             - the address in ALLOWED_RECIPIENTS, then a deploy")
        print("             - WEBHOOK_URL on the Worker = this host's public origin")
        print("           The Worker logs `Ignored mail to ...` for a refused one;")
        print("           no log line at all means Email Routing never called it.")
    else:
        print(f"  receipts {len(rows)}, newest first:")
        for store, row in rows:
            when = row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "?"
            print(f"    {when}  {row.outcome:10} store={store}")
            print(f"      from {row.from_address}  subject {row.subject!r}")
            if row.detail:
                print(f"      {row.detail[:120]}")
            if row.outcome == "accepted":
                where = f"/{store}/app" if store and "unprefixed" not in store else "/app"
                print(f"      filed: lead={row.lead_id} -- readable under {where}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
