"""A person's own email sign-off, and the image under it.

**Everybody edits their own and nobody edits anybody else's.** A signature
carries somebody's name and title; a manager rewriting a rep's is putting words
under that rep's name in front of a buyer. There is deliberately no admin view
of these -- `require_manager` guards the roster and the assistant's
instructions, which are the dealership's, and this is not.

**The image is served publicly, and that is a requirement rather than a
shortcut.** What loads it is the recipient's mail client: no session, no
cookie, and no way to obtain either. Behind `current_user` it would render as a
broken image in every email the dealership sends. The token is random rather
than the user id, so somebody who received one email cannot walk the staff list
from it -- the same reasoning `click_token` and `reply_token` already follow.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import outreach_send
from app.api.deps import current_user
from app.config import BACKEND_DIR, settings
from app.db import get_db
from app.models import User, UserSignature
from app.schemas.serialize import stamp

router = APIRouter(tags=["signature"])

#: What a mail client will actually render. No SVG: it is a document format
#: that can carry script, and this one is served to strangers from our origin.
IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}

#: A sign-off, not a newsletter. Big enough for a name, a title, a phone number
#: and a line of address; small enough that nobody pastes a disclaimer into it.
MAX_TEXT = 500

#: Inline images are fetched on every open by every recipient. A logo is a few
#: kilobytes; a megabyte here is somebody's uncropped photo.
MAX_IMAGE_BYTES = 512 * 1024


def _folder():
    path = BACKEND_DIR / "var" / "signatures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _row(db: Session, user: User) -> UserSignature:
    row = db.query(UserSignature).filter_by(user_id=user.id).one_or_none()
    if row is None:
        row = UserSignature(user_id=user.id, text="")
        db.add(row)
        db.commit()
    return row


def _out(db: Session, row: UserSignature, request: Request) -> dict:
    base = settings.public_base_url or str(request.base_url)
    return {
        "text": row.text or "",
        "image_url": (
            f"{base.rstrip('/')}/s/{row.image_token}.{row.image_ext or 'png'}"
            if row.image_token else ""
        ),
        "updated_at": stamp(row.updated_at),
        # What goes out when this person has written nothing. Shown so the
        # editor can say "leave it empty and you get this" rather than leaving
        # somebody guessing what an empty box means.
        "fallback": outreach_send.signature(db),
        "max_chars": MAX_TEXT,
        "max_image_kb": MAX_IMAGE_BYTES // 1024,
    }


@router.get("/me/signature")
def read_signature(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    return _out(db, _row(db, user), request)


class SignatureBody(BaseModel):
    text: str


@router.put("/me/signature")
def write_signature(
    body: SignatureBody,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    text = (body.text or "").strip()
    if len(text) > MAX_TEXT:
        raise HTTPException(
            400, f"A sign-off is at most {MAX_TEXT} characters; that is {len(text)}."
        )
    row = _row(db, user)
    row.text = text
    db.commit()
    return _out(db, row, request)


@router.post("/me/signature/image")
async def upload_signature_image(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    ext = IMAGE_TYPES.get((file.content_type or "").lower())
    if ext is None:
        raise HTTPException(
            400,
            "That is not an image a mail client will render. PNG, JPEG, GIF or "
            "WebP -- and not SVG, which can carry script and is served from our "
            "own origin to strangers.",
        )
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "That file was empty.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(
            400,
            f"That is {len(raw) // 1024} KB; the limit is {MAX_IMAGE_BYTES // 1024} KB. "
            "It is fetched on every open by every recipient.",
        )

    row = _row(db, user)
    old = (
        _folder() / f"{row.image_token}.{row.image_ext}"
        if row.image_token else None
    )
    # A fresh token per upload, so a replaced image is not served from a cache
    # keyed on the old URL -- and the old file is removed rather than left to
    # accumulate under a name nothing points at.
    row.image_token = secrets.token_urlsafe(18).replace("-", "").replace("_", "")[:24]
    row.image_ext = ext
    (_folder() / f"{row.image_token}.{ext}").write_bytes(raw)
    db.commit()
    if old is not None and old.exists():
        old.unlink()
    return _out(db, row, request)


@router.delete("/me/signature/image")
def remove_signature_image(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    row = _row(db, user)
    if row.image_token:
        path = _folder() / f"{row.image_token}.{row.image_ext}"
        if path.exists():
            path.unlink()
    row.image_token = ""
    row.image_ext = ""
    db.commit()
    return _out(db, row, request)


#: No `/api` prefix and no session: this is the URL a recipient's mail client
#: fetches, exactly like `/r/<token>` is the one their browser follows.
public = APIRouter(tags=["signature"])


@public.get("/s/{name}")
def serve_signature_image(name: str, db: Session = Depends(get_db)):
    """The image itself. Looked up by token, never by user id.

    The filename on disk is rebuilt from the row rather than taken from the
    URL, so a path separator or a `..` in `name` reaches nothing -- the token
    either matches a row or it does not.
    """
    token = name.rsplit(".", 1)[0]
    row = (
        db.query(UserSignature).filter_by(image_token=token).one_or_none()
        if token else None
    )
    if row is None or not row.image_token:
        raise HTTPException(404, "No such image")
    path = _folder() / f"{row.image_token}.{row.image_ext}"
    if not path.exists():
        raise HTTPException(404, "No such image")
    return FileResponse(
        path,
        media_type=f"image/{row.image_ext}",
        # Cached hard: the token changes whenever the image does, so a stale
        # copy is impossible and every open of every email would otherwise be
        # a request.
        headers={"Cache-Control": "public, max-age=604800"},
    )
