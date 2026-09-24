"""The lots of a dealer group, and which one each car stands on.

A group is one database, so a manager works across all of its lots -- and a
car is on exactly one of them, and a visit to see it is booked there. Craig
and Landreth's export is three stores in one file (Louisville, Clarksville IN,
Bullitt County); before this every visit was booked at the single address in
`dealership`, and a buyer who wanted a Clarksville car was told it was
somewhere else and then booked where it was not.

**The profile is the source, the table is what points can point at.** Lots
are facts about the dealership, so they are written in its profile
(`locations:`, read by `profile.locations`) like its address and hours; they
are rows because a car and an appointment have to name one. `sync` makes the
rows say what the profile says, at every boot and seed and after an import,
and changes nothing when nothing changed. A dealership that lists no lots has
one: its own address, key `main`.

**The primary is the dealership row, mirrored.** Its address, phone and hours
are the ones at the top of the profile, so they are copied from the row the
seed built rather than written twice.

**A lot is where you can book only if we know where it is and when it
opens.** No street address or no hours on file and its cars are still placed
there and still say so, but a visit is booked at the primary: nothing here
could tell the buyer where to go, and inventing it is the one thing this
codebase will not do.

**A car is placed by what its own row says**: the store id in the feed
(`dealer_id`, from Dealer Car Search) or the lot name in the export
(`location`), matched against each lot's `match` list -- or, for a name no
lot lists, against the lot's own address, which is how "Santa Ana" on every
one of Alsbou's cars finds the Santa Ana showroom. A car that says nothing is
at the primary. A car that names a lot nobody has described stays unplaced,
and its own words still say where it is.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app import profile
from app.models import Appointment, Dealership, Location, Vehicle

#: The key the only lot of a dealership that lists none gets.
MAIN = "main"


def _wanted(db: Session) -> list[dict]:
    """The rows as the profile says they should be, primary filled from the dealership."""
    shop = db.query(Dealership).first()
    listed = profile.locations() or [
        {"key": MAIN, "name": shop.name if shop else "", "address": "", "phone": "",
         "hours": {}, "match": [], "primary": True}
    ]
    out = []
    for spec in listed:
        if spec["primary"] and shop is not None:
            spec = {**spec, "address": shop.address or "", "phone": shop.phone or "",
                    "hours": json.loads(shop.hours_json or "{}")}
        out.append(spec)
    return out


def sync(db: Session) -> dict:
    """Make `locations` say what the profile says, then place every car.

    Returns what changed, by key, so a caller can say it. A lot the profile
    stops listing is switched off rather than deleted: the visits booked there
    are history somebody still reads, and they point at its row.
    """
    changed: dict = {"added": [], "updated": [], "retired": [], "placed": 0}
    rows = {r.key: r for r in db.query(Location).all()}
    listed: set[str] = set()
    for position, spec in enumerate(_wanted(db)):
        listed.add(spec["key"])
        values = {
            "name": spec["name"],
            "address": spec["address"],
            "phone": spec["phone"],
            "hours_json": json.dumps(spec["hours"]) if spec["hours"] else "",
            "aliases_json": json.dumps(spec["match"]),
            "is_primary": spec["primary"],
            "active": True,
            "position": position,
        }
        row = rows.get(spec["key"])
        if row is None:
            db.add(Location(key=spec["key"], **values))
            changed["added"].append(spec["key"])
        elif any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            changed["updated"].append(spec["key"])
    for key, row in rows.items():
        if key not in listed and (row.active or row.is_primary):
            row.active, row.is_primary = False, False
            changed["retired"].append(key)
    db.flush()
    changed["placed"] = place(db)
    db.commit()
    return changed


class Lots:
    """Every lot of this group, read once for a whole tool call or page.

    A search returns five cars and a storefront page twenty-four; asking the
    table once per car would be the same answer read over and over.
    """

    def __init__(self, db: Session) -> None:
        rows = db.query(Location).order_by(Location.position, Location.key).all()
        self.by_id = {r.id: r for r in rows}
        self.active = [r for r in rows if r.active]
        self.primary = next((r for r in self.active if r.is_primary), None) or (
            self.active[0] if self.active else None
        )
        self._index: dict[str, Location] = {}
        for row in self.active:
            for alias in json.loads(row.aliases_json or "[]"):
                self._index.setdefault(alias, row)

    @property
    def several(self) -> bool:
        """More than one lot -- the only time a car's lot is worth saying."""
        return len(self.active) > 1

    def by_key(self, key: str) -> Location | None:
        key = (key or "").strip().lower()
        return next((r for r in self.active if r.key == key), None)

    def place(self, raw: dict) -> Location | None:
        """The lot a car's own row says it is on, or None when it names one
        nobody has described."""
        feed = str(raw.get("dealer_id") or "").strip().lower()
        stated = str(raw.get("location") or "").strip().lower()
        for said in (feed, stated):
            if said and said in self._index:
                return self._index[said]
        if stated:
            # A lot name nobody listed, which may still be the lot's own town:
            # Alsbou's export stamps "Santa Ana" on every car, and that is where
            # the showroom is.
            for row in self.active:
                if row.address and stated in row.address.lower():
                    return row
            return None
        if not self.several:
            # One lot, and a store id its own crawl kept: that lot. A feed id
            # only tells lots apart where there is more than one to tell.
            return self.primary
        return None if feed else self.primary

    def of(self, vehicle: Vehicle | None) -> Location | None:
        placed = getattr(vehicle, "location_id", None) if vehicle is not None else None
        return self.by_id.get(placed) if placed else None

    def for_visit(self, vehicle: Vehicle | None) -> Location | None:
        """Where a visit to see this car is booked: its own lot when a buyer
        could be told where that is and when it opens, the primary otherwise."""
        lot = self.of(vehicle)
        return lot if lot is not None and bookable(lot) else self.primary

    def of_appointment(self, appointment: Appointment) -> Location | None:
        """The lot a visit is at. None on the row is a visit booked before the
        group had more than one, which was the primary."""
        if appointment.location_id and appointment.location_id in self.by_id:
            return self.by_id[appointment.location_id]
        return self.primary


def place(db: Session) -> int:
    """Put every car on the lot its row names. Returns how many moved."""
    lots = Lots(db)
    moved = 0
    for vehicle in db.query(Vehicle).all():
        raw = json.loads(vehicle.raw_json or "{}") if vehicle.raw_json else {}
        lot = lots.place(raw if isinstance(raw, dict) else {})
        target = lot.id if lot is not None else None
        if vehicle.location_id != target:
            vehicle.location_id = target
            moved += 1
    return moved


def at(lots: Lots, lot: Location | None, shop: Dealership | None) -> tuple[str, str]:
    """(where, phone) for a sentence telling a buyer where their visit is.

    "our Clarksville store, <address>" in a group, the bare address where
    there is one lot; the lot's own number where it has one. Falls back to the
    dealership's row for anything the lot does not carry, which is only ever
    the primary's own values again.
    """
    address = (lot.address if lot is not None and lot.address else "") or (shop.address if shop else "")
    phone = (lot.phone if lot is not None and lot.phone else "") or (shop.phone if shop else "")
    if lots.several and lot is not None:
        return f"our {lot.name} store, {address}", phone
    return address, phone


def hours(lot: Location | None) -> dict:
    return json.loads(lot.hours_json) if lot is not None and lot.hours_json else {}


def bookable(lot: Location | None) -> bool:
    """A visit can be booked at this lot: a street address and hours on file."""
    return bool(lot is not None and lot.address.strip() and any(hours(lot).values()))


def out(lot: Location | None) -> dict | None:
    """A lot as a page or a tool result carries it."""
    if lot is None:
        return None
    return {
        "id": lot.id,
        "key": lot.key,
        "name": lot.name,
        "address": lot.address,
        "phone": lot.phone,
        "primary": lot.is_primary,
        "bookable": bookable(lot),
    }


def stores():
    """(slug, session) for every seeded store this process serves, each once.

    The default store is whatever `DEALERSHIP=` names -- the lifespan's
    meaning, and not `SessionLocal("")`, which is always `liner.db` -- and a
    store the default already is is not visited twice. The profile readers
    follow `current_store`, so each session is opened with its store set.
    """
    from app import mailboxes
    from app.config import settings
    from app.db import SessionLocal, has_database
    from app.stores import known_stores

    default = settings.dealership.strip()
    seen: set[str] = set()
    for slug in [default] + [s for s in known_stores() if has_database(s)]:
        url = settings.database_url_for(slug)
        if url in seen:
            continue
        seen.add(url)
        with mailboxes.using(slug):
            db = SessionLocal(slug)
            try:
                seeded = db.query(Dealership).first() is not None
            except Exception:  # noqa: BLE001 -- a database with no tables yet
                seeded = False
            if not seeded:
                db.close()
                continue
            yield slug, db


def sync_all() -> None:
    """Every seeded store's lots in step with its profile. Run at boot."""
    import logging

    log = logging.getLogger("liner.locations")
    for slug, db in stores():
        with db:
            what = sync(db)
        moved = {k: v for k, v in what.items() if v}
        if moved:
            log.info("locations for %s: %s", slug or "the default store", moved)


def main() -> int:
    """`make locations`: bring every seeded store's lots in step with its profile.

    The boot does the same; this is for a profile edited on a running box,
    where the rows change at once and the answer is on screen.
    """
    for slug, db in stores():
        with db:
            what = sync(db)
            lots = Lots(db)
            print(f"\n{slug or 'default store'}:")
            for lot in lots.active:
                cars = db.query(Vehicle).filter(Vehicle.location_id == lot.id).count()
                where = lot.address or "no street address on file"
                book = "books visits" if bookable(lot) else "visits booked at the primary"
                print(f"  {lot.name:18} {cars:>4} cars   {where}   ({book})")
            unplaced = db.query(Vehicle).filter(Vehicle.location_id.is_(None)).count()
            if unplaced:
                print(f"  {unplaced} car(s) name a lot this profile does not describe")
            moved = [f"{k} {', '.join(what[k])}" for k in ("added", "updated", "retired") if what[k]]
            if moved or what["placed"]:
                print(f"  changed: {'; '.join(moved) or 'nothing'}; {what['placed']} car(s) placed")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
