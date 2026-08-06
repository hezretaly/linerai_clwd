from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.config import settings
from app.db import get_db
from app.ingest.csv_import import COLUMNS, import_csv
from app.ingest.pipeline import IngestError, publish, run_ingest
from app.models import IngestRun, User
from app.schemas.serialize import ingest_run_out

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.get("/runs")
def list_runs(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    rows = db.query(IngestRun).order_by(IngestRun.started_at.desc()).limit(25).all()
    return {
        "runs": [ingest_run_out(r) for r in rows],
        "source_url": settings.scraper_base_url,
        "configured": bool(settings.scraper_base_url),
        "csv_columns": COLUMNS,
        "detail": (
            f"Ingesting from {settings.scraper_base_url}."
            if settings.scraper_base_url
            else "No dealer website is configured. Set SCRAPER_BASE_URL to crawl a site, "
                 "or upload a CSV -- the CSV path needs no configuration."
        ),
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    run = db.query(IngestRun).filter_by(id=run_id).one_or_none()
    if run is None:
        raise HTTPException(404, "Run not found")
    return ingest_run_out(run)


@router.post("/runs")
def start_run(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if not settings.scraper_base_url:
        raise HTTPException(
            503,
            {
                "error": "not_configured",
                "integration": "scraper",
                "missing": ["SCRAPER_BASE_URL"],
                "detail": "No dealer website is configured. Upload a CSV instead.",
            },
        )
    run = run_ingest(db, settings.scraper_base_url)
    return ingest_run_out(run)


@router.post("/csv")
async def upload_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    if not raw.strip():
        raise HTTPException(400, "Empty file")
    run = import_csv(db, raw)
    return ingest_run_out(run)


@router.post("/runs/{run_id}/publish")
def publish_run(
    run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    """Applies a reviewed diff. Nothing reaches the live table before this."""
    run = db.query(IngestRun).filter_by(id=run_id).one_or_none()
    if run is None:
        raise HTTPException(404, "Run not found")
    try:
        applied = publish(db, run)
    except IngestError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"run": ingest_run_out(run), "applied": applied}
