"""CSV import -- the fallback that always works.

Twenty lines that save scraper projects. It is also the only inventory path
available when no dealer site is configured, which is the default state.
"""

from __future__ import annotations

import csv
import io
import json

from sqlalchemy.orm import Session

from app.db import utcnow
from app.ingest.extract import to_int, valid_vin
from app.models import IngestRun, Vehicle

COLUMNS = "vin,year,make,model,trim,price,mileage,body_style,seats"


def import_csv(db: Session, raw: str) -> IngestRun:
    run = IngestRun(source_url="csv upload", method="csv", status="pending")
    db.add(run)
    db.commit()

    reader = csv.DictReader(io.StringIO(raw))
    existing = {v.vin: v for v in db.query(Vehicle).all()}
    created, updated, errors = [], [], []

    for index, row in enumerate(reader, start=2):
        vin = (row.get("vin") or "").upper().strip()
        if not valid_vin(vin):
            errors.append({"row": index, "error": f"VIN {vin!r} is not 17 valid characters"})
            continue
        payload = {
            "vin": vin,
            "year": to_int(row.get("year")),
            "make": (row.get("make") or "").strip(),
            "model": (row.get("model") or "").strip(),
            "trim": (row.get("trim") or "").strip(),
            "price": to_int(row.get("price")),
            "mileage": to_int(row.get("mileage")),
            "body_style": (row.get("body_style") or "").strip(),
            "seats": to_int(row.get("seats")),
        }
        if vin in existing:
            current = existing[vin]
            manual = set(json.loads(current.manual_fields_json or "[]"))
            changes = {
                key: {"from": getattr(current, key, None), "to": value}
                for key, value in payload.items()
                if key != "vin" and value not in (None, "") and key not in manual
                and getattr(current, key, None) != value
            }
            if changes:
                updated.append({"vin": vin, "changes": changes,
                                "protected": sorted(manual & set(payload))})
        else:
            created.append(payload)

    diff = {"created": created, "updated": updated, "removed": []}
    run.listings_found = len(created) + len(updated)
    run.created_count = len(created)
    run.updated_count = len(updated)
    run.diff_json = json.dumps(diff, default=str)
    run.errors_json = json.dumps(errors)
    run.status = "ready" if (created or updated) else "failed"
    if run.status == "failed" and not errors:
        run.errors_json = json.dumps([{"error": "Nothing to import."}])
    run.finished_at = utcnow()
    db.commit()
    db.refresh(run)
    return run
