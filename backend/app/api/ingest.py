from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app import profile
from app.config import settings
from app import mailboxes
from app.db import SessionLocal, active_store, get_db
from app.ingest import browser, pipeline
from app.ingest.csv_import import COLUMNS, import_csv
from app.ingest.extract import list_adapter_named
from app.ingest.pipeline import IngestError, publish
from app.models import IngestRun, User
from app.schemas.serialize import ingest_run_out

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.get("/runs")
def list_runs(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    running = pipeline.in_progress(db)
    rows = db.query(IngestRun).order_by(IngestRun.started_at.desc()).limit(25).all()
    source = profile.inventory()
    named = list_adapter_named(source.get("adapter", ""))
    needs_browser = bool(named is not None and named.browser)
    return {
        "runs": [ingest_run_out(r) for r in rows],
        "source_url": source["source_url"],
        "configured": bool(source["source_url"]),
        "csv_columns": COLUMNS,
        # A crawl in flight, so the page can show it and not offer a second.
        "running": ingest_run_out(running) if running else None,
        # Said before the button is pressed, for the same reason the source
        # is: a box that cannot run the browser this site needs should say
        # so here, not as a failed run a minute later.
        "browser": {"needed": needs_browser,
                    "problem": browser.problem() if needs_browser else ""},
        # Whether "also read each car's own page" means anything for this
        # site: only a reader that has a page to read offers it.
        "details": {"supported": bool(named is not None and named.reads_details)},
        # Naming the source *and where it was read from* before the button is
        # pressed. A crawl of the wrong dealer's site succeeds exactly like a
        # crawl of the right one, so the moment to notice is now.
        "detail": _source_detail(source),
    }


def _source_detail(source: dict) -> str:
    if not source["source_url"]:
        return (
            "No dealer website is configured. Put an `inventory.source_url` in this "
            "dealership's profile to crawl their site, or upload a CSV -- the CSV path "
            "needs no configuration."
        )
    where = {
        "profile": f"from {settings.dealership_config.name}",
        "env": "from SCRAPER_BASE_URL in .env",
    }.get(source["origin"], "")
    lot = f", store {source['dealer_id']}" if source["dealer_id"] else ""
    return f"Your cars are read from {source['source_url']}{lot} ({where})."


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    run = db.query(IngestRun).filter_by(id=run_id).one_or_none()
    if run is None:
        raise HTTPException(404, "Run not found")
    return ingest_run_out(run)


class RunBody(BaseModel):
    #: Read every car's own page too, for its options list. The page a price
    #: change needs is read either way.
    details: bool = False
    #: Read those pages again even where a read is kept from before.
    refresh_details: bool = False


class PublishBody(BaseModel):
    #: A person saying the cars the crawl did not see really have sold.
    allow_removals: bool = False


@router.post("/runs")
def start_run(background: BackgroundTasks, body: RunBody | None = Body(None),
              db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Start a crawl and answer at once; the page polls the run.

    It ran inside the request, which was fine while a crawl was a few plain
    fetches. With a browser even a list read takes several seconds, and one
    with each car's page takes minutes -- past Cloudflare's hundred-second
    limit, which ends the request with a 524 while the crawl carries on
    unseen. So every crawl goes to the background: one path, not two.
    """
    source = profile.inventory()
    if not source["source_url"]:
        raise HTTPException(
            503,
            {
                "error": "not_configured",
                "integration": "scraper",
                "missing": ["inventory.source_url"],
                "detail": (
                    "No dealer website is configured for "
                    f"{settings.dealership_config.name}. Add an `inventory.source_url` to "
                    "their profile, or upload a CSV instead."
                ),
            },
        )
    busy = pipeline.in_progress(db)
    if busy is not None:
        raise HTTPException(409, {"error": "in_progress",
                                  "detail": "A refresh is already running. It will show here when it finishes.",
                                  "run": ingest_run_out(busy)})
    run = pipeline.start_run(db, source["source_url"])
    background.add_task(_crawl_later, active_store(), run.id,
                        bool(body and body.details), bool(body and body.refresh_details))
    return ingest_run_out(run)


def _crawl_later(slug: str, run_id: str, details: bool, refresh: bool) -> None:
    """The crawl, after the response. Its own session, because the request's
    is closed by now, and its own store, set for the whole pass so the
    profile, the database and the snapshot folder are all that store's --
    the same shape as the email intake's `_place`."""
    with mailboxes.using(slug):
        db = SessionLocal()
        try:
            run = db.get(IngestRun, run_id)
            if run is not None and run.status == "pending":
                pipeline.run_ingest(db, run.source_url, run=run,
                                    details=details, refresh_details=refresh)
        finally:
            db.close()


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
    run_id: str, body: PublishBody | None = Body(None),
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    """Applies a reviewed diff. Nothing reaches the live table before this.

    And asks first when it would take a large share of the lot off sale, or
    when the crawl behind it was incomplete -- the check `make ingest` always
    made and this button never did.
    """
    run = db.query(IngestRun).filter_by(id=run_id).one_or_none()
    if run is None:
        raise HTTPException(404, "Run not found")
    if run.status == "ready" and not (body and body.allow_removals):
        diff = json.loads(run.diff_json or "{}")
        risk = pipeline.removal_risk(db, diff, json.loads(run.errors_json or "[]"))
        if risk:
            raise HTTPException(409, {"error": "removals", **risk})
    try:
        applied = publish(db, run)
    except IngestError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"run": ingest_run_out(run), "applied": applied}
