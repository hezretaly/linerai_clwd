from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.integrations.registry import registry_payload

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
        db_error = ""
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    payload = registry_payload()
    return {
        "status": "ok" if db_ok else "degraded",
        "env": settings.env,
        "database": {"ok": db_ok, "error": db_error},
        **payload,
    }


@router.get("/integrations")
def integrations() -> dict:
    """What is real and what is a placeholder. Drives the amber banner in the UI."""
    return registry_payload()
