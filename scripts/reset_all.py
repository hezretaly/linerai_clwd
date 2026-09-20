#!/usr/bin/env python3
"""Delete and reseed EVERY dealership this deployment can serve.

    make reset-all

`make reset-db` acts on one store -- whichever `DEALERSHIP=` names -- so on a
host serving several dealerships it seeded the one in `.env` and left the
others without a database, and their storefronts answered 503 (or, before
that was caught, minted an empty file and answered 500). This runs the same
two commands `reset-db` runs, once per profile, with `DEALERSHIP=<slug>` set
for each: `scripts/drop_db.py` then `app.seed`. The same commands rather than
a second seeding path, because a second path is how one of them stops
creating the profile's staff.

Every store's block is printed in full -- each profile's manager and reps
are created by its own seed with passwords shown once, and this is the one
place all of them scroll past together, so the headers name the store.

It deletes every store's leads, conversations and appointments. That is what
`reset-db` does too; this is simply all of them. `ops.db` is untouched.
`--only a,b` limits it to some slugs; `--dry-run` prints the plan.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = ROOT / "backend" / ".venv" / "bin" / "python"


def slugs() -> list[str]:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.config import settings

    return list(settings.store_slugs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help="comma-separated slugs; default is every profile")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wanted = [s.strip() for s in args.only.split(",") if s.strip()] or slugs()
    unknown = [s for s in wanted if s not in slugs()]
    if unknown:
        print(f"no profile for: {unknown}; known: {slugs()}", file=sys.stderr)
        return 2

    for slug in wanted:
        print(f"\n{'=' * 72}\n== {slug}\n{'=' * 72}")
        env = {**os.environ, "DEALERSHIP": slug}
        steps = [
            [str(PY), str(ROOT / "scripts" / "drop_db.py")],
            [str(PY), "-m", "app.seed"],
        ]
        for i, cmd in enumerate(steps):
            cwd = ROOT / "backend" if i == 1 else ROOT
            if args.dry_run:
                print(f"  (dry run) DEALERSHIP={slug} {' '.join(cmd)}  [cwd {cwd.name}]")
                continue
            code = subprocess.call(cmd, env=env, cwd=cwd)
            if code != 0:
                print(f"\n{slug}: step {i + 1} exited {code}; stopping here so the failure is the last thing on screen.")
                return code
    if not args.dry_run:
        print(f"\nDone: {len(wanted)} store(s) reseeded. `make stores` lists them; restart the server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
