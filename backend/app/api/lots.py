"""The group's lots, for the dashboard: `GET /api/locations`.

What the calendar labels a visit with and what a rep reads to know which
showroom a buyer is driving to. Each lot says whether a visit can be booked
there -- a street address and hours on file -- because one that cannot is a
gap somebody has to fill in the profile, and a gap nobody is shown stays one.
Written in the profile, not here: `make locations` after editing it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import locations
from app.api.deps import current_user
from app.db import get_db
from app.models import User, Vehicle

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("")
def list_locations(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    lots = locations.Lots(db)
    counts = dict(
        db.query(Vehicle.location_id, func.count(Vehicle.id))
        .filter(Vehicle.status == "available", Vehicle.location_id.isnot(None))
        .group_by(Vehicle.location_id)
        .all()
    )
    unplaced = (
        db.query(func.count(Vehicle.id))
        .filter(Vehicle.status == "available", Vehicle.location_id.is_(None))
        .scalar() or 0
    )
    return {
        "several": lots.several,
        "locations": [
            {**locations.out(lot), "cars": counts.get(lot.id, 0)} for lot in lots.active
        ],
        # Cars naming a lot the profile does not describe. Said, because a
        # count that quietly leaves them out reads as a smaller lot.
        "unplaced": unplaced,
    }
