"""Reading mail and handling its files: the reader, uploads and downloads.

Both realms live here -- a dealership's mail under `/api/email/...` and
Liner's own under `/api/ops/mail/...` -- because what they share is the hard
part: HTML that must be cleaned before a browser draws it, `cid:` images that
must become `data:` URIs, and files that must never be served from our own
origin as anything a browser would render.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["mail-reader"])
