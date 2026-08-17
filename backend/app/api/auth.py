from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session,
    current_account,
    set_session,
    staff_query,
    verify_password,
)
from app.config import settings
from app.db import get_db
from app.models import OpsUser, User
from app.ratelimit import SlidingWindow
from app.schemas.serialize import user_out

log = logging.getLogger("liner.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

#: Wrong passwords, per account. See `app/ratelimit.py` for why the key is the
#: address rather than the caller -- in short, because behind a proxy an IP key
#: locks out everybody at once.
attempts = SlidingWindow(settings.login_max_attempts, settings.login_window_seconds)


class LoginBody(BaseModel):
    # Plain str, not EmailStr: the seeded accounts use @example.invalid, which
    # RFC 2606 reserves so a misfire can never reach a real stranger. Strict
    # validators reject it, and it is only ever a lookup key here.
    email: str
    password: str


@router.post("/login")
def login(
    body: LoginBody,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """One form, two tables.

    The dealership's staff are in `users` and we are in `ops_users`, and the
    address decides which -- not a toggle on the form, which would be a way to
    probe whether an address exists on the other side. The wrong-password
    message is identical either way for the same reason.

    Rate limited per account. This is a public form on a public host and the
    password is the only thing in front of a dealership's buyer list and of
    `/ops`; without a limit, nothing slowed a guess down or recorded that one
    was happening.
    """
    email = body.email.strip().lower()

    # Asked before the attempt, and identical whether or not the address
    # exists -- a limit that only bites on real accounts is an account
    # enumeration oracle, which is a worse leak than the one it is guarding.
    wait = attempts.retry_after(email)
    if wait:
        log.warning("login rate limited for %s -- %ss remaining", email, wait)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many attempts. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )

    account = (
        db.query(User).filter_by(email=email, active=True).one_or_none()
        or db.query(OpsUser).filter_by(email=email, active=True).one_or_none()
    )
    if account is None or not verify_password(body.password, account.password_hash):
        attempts.record(email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")

    attempts.clear(email)
    set_session(response, account)
    return {"user": user_out(account)}


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
def me(account=Depends(current_account)) -> dict:
    """Either realm -- this is the one question that has to answer for both.

    The dashboards branch on the role that comes back, so a 403 here would
    mean an ops session could not even discover it was signed in.
    """
    return {"user": user_out(account)}
