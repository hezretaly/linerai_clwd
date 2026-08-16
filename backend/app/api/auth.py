from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session,
    current_user,
    set_session,
    staff_query,
    verify_password,
)
from app.config import settings
from app.db import get_db
from app.models import User
from app.schemas.serialize import user_out

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    # Plain str, not EmailStr: the seeded accounts use @example.invalid, which
    # RFC 2606 reserves so a misfire can never reach a real stranger. Strict
    # validators reject it, and it is only ever a lookup key here.
    email: str
    password: str


@router.post("/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)) -> dict:
    user = db.query(User).filter_by(email=body.email.lower(), active=True).one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    set_session(response, user)
    return {"user": user_out(user)}


def demo_rep(db: Session) -> User | None:
    """The account the public door opens as, or None when it is shut.

    A real rep row, deliberately. The alternative -- a synthetic user, or
    skipping `current_user` when the flag is on -- would mean every role check
    in the system has a second path through it, and the one that matters most
    (`require_manager`, which guards the team page, settings and publishing) is
    the one nobody would think to test on that path. This way a public visitor
    is a rep in exactly the sense the rest of the code already means.
    """
    if not settings.public_demo:
        return None
    # A rep, always. `staff_query` is what keeps PUBLIC_DEMO_EMAIL from being
    # able to name an owner -- the door that lets a stranger in with no
    # password must not be a way into Liner's own dashboard, and a typo in one
    # `.env` line should not be all that stands between the two.
    query = staff_query(db).filter(User.role == "rep")
    if settings.public_demo_email:
        return query.filter(User.email == settings.public_demo_email.lower()).one_or_none()
    return query.order_by(User.name.asc()).first()


@router.get("/public")
def public_demo(db: Session = Depends(get_db)) -> dict:
    """Is the door open, and who does it lead in as?

    Unauthenticated by necessity -- it is the question asked *before* signing
    in. It reveals only that a demo exists and the name on the seat, which is
    already on every page that visitor is about to be shown.
    """
    rep = demo_rep(db)
    if rep is None:
        return {"available": False}
    return {"available": True, "name": rep.name, "role": rep.role}


@router.post("/public")
def enter_public_demo(response: Response, db: Session = Depends(get_db)) -> dict:
    """Let a visitor in as that rep, with no password."""
    rep = demo_rep(db)
    if rep is None:
        # 404 rather than 403: with the flag off there is no such door, and
        # saying "forbidden" tells a stranger there is one to look for.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    set_session(response, rep)
    return {"user": user_out(rep), "demo": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    clear_session(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"user": user_out(user)}
