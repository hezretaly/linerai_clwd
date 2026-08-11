from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app import events
from app.config import settings
from app.db import SessionLocal, create_all
from app.integrations.base import NotConfigured
from app.integrations.registry import registry_payload

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("liner")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()

    # Sync endpoints run in a threadpool; events.emit needs a handle on the
    # main loop to reach connected sockets from there.
    events.bind_loop(asyncio.get_running_loop())

    # The WebSocket connection manager is in-process (§5.2), so more than one
    # worker silently breaks realtime for whichever workers a client misses.
    workers = int(os.environ.get("WEB_CONCURRENCY", "1") or 1)
    if workers > 1:
        log.warning(
            "WEB_CONCURRENCY=%s but the event bus is in-process. Dealer sockets will "
            "miss events. Run a single worker until events.py moves to Redis pub/sub.",
            workers,
        )

    unconfigured = registry_payload()["unconfigured"]
    if unconfigured:
        log.warning(
            "%d integration(s) not configured: %s. Affected features report themselves "
            "as unavailable rather than simulating a result.",
            len(unconfigured), ", ".join(unconfigured),
        )
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Liner AI", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(NotConfigured)
    async def _not_configured(request: Request, exc: NotConfigured) -> JSONResponse:
        # 503, not 500: the feature exists, the credential does not.
        return JSONResponse(status_code=503, content=exc.as_dict())

    from app.api import (
        appointments,
        auth,
        chat,
        conversations,
        health,
        inbound_email,
        ingest,
        inventory,
        lead_import,
        leads,
        mailbox,
        outreach,
        overview,
        redirect,
        settings as settings_api,
        team,
        voice,
        ws,
    )

    for router in (
        health.router,
        auth.router,
        chat.router,
        overview.router,
        conversations.router,
        leads.router,
        lead_import.router,
        appointments.router,
        outreach.router,
        inbound_email.router,
        mailbox.router,
        inventory.router,
        ingest.router,
        voice.router,
        team.router,
        settings_api.router,
    ):
        app.include_router(router, prefix="/api")

    # No /api prefix: the socket lives at /ws/dealer.
    app.include_router(ws.router)
    # Nor here: /r/<token> is a link a buyer follows from their inbox, and
    # it has to be short enough to read in an email client's status bar.
    app.include_router(redirect.router)

    @app.get("/api/photos/{vin}.svg")
    def vehicle_photo(vin: str) -> Response:
        from app.models import Vehicle
        from app.photos import placeholder_svg

        db = SessionLocal()
        try:
            vehicle = db.query(Vehicle).filter_by(vin=vin.upper()).one_or_none()
            if vehicle is None:
                svg = placeholder_svg(vin, 0, "Unknown", "vehicle")
            else:
                svg = placeholder_svg(
                    vehicle.vin, vehicle.year, vehicle.make, vehicle.model, vehicle.trim
                )
        finally:
            db.close()
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # Last, so its catch-all cannot shadow a real route. No-op until the
    # frontend is built, which is what keeps `make dev` on Vite.
    from app.static import mount_frontend

    if mount_frontend(app):
        log.info(
            "Serving the built frontend from frontend/dist."
            if settings.is_production
            else "frontend/dist exists, so this process also serves it. In development "
                 "use Vite on :5173 -- the build on :8000 is whatever `make build` last "
                 "produced and does not hot-reload."
        )
    elif settings.is_production:
        log.warning(
            "ENV=production but frontend/dist is missing. The API will answer /api "
            "and /ws only. Run `make build` to serve the site from this process."
        )

    return app


app = create_app()
