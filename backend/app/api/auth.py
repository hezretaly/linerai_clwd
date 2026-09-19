from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import (
    clear_session,
    current_account,
    pwd,
    set_session,
    staff_query,
    verify_password,
)
from app.config import settings
from app.db import SessionLocal, current_store, get_db, has_database, ops_session
from app.models import OpsUser, User
from app.ratelimit import SlidingWindow
from app.stores import known_stores
from app.schemas.serialize import user_out

log = logging.getLogger("liner.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

#: Wrong passwords, per account. See `app/ratelimit.py` for why the key is the
#: address rather than the caller -- in short, because behind a proxy an IP key
#: locks out everybody at once.
attempts = SlidingWindow(settings.login_max_attempts, settings.login_window_seconds)

#: A hash to check against when the address matches nobody.
#:
#: Without it, an unknown address returned in about 2ms while a real one paid
#: bcrypt and took about 265ms -- so anyone could ask "is founder@ an account
#: here?" and read the answer off a stopwatch, whatever the response body said.
#: Measured, not assumed: that 263ms gap was the reason this exists.
#:
#: Hashed from a random value at import rather than a constant in the source,
#: so there is no password on earth that matches it. It costs one bcrypt at
#: startup and makes the two paths take the same shape.
_ABSENT_HASH = pwd.hash(secrets.token_hex(32))


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

    slug = locate_store(email)

    with SessionLocal(slug) as store_db, ops_session() as ops:
        # Two databases, one form. The dealership's staff are in this store's
        # file; we are in Liner's own, which is not per store -- so an owner
        # signing in is found once however many dealerships exist.
        account = (
            store_db.query(User).filter_by(email=email, active=True).one_or_none()
            or ops.query(OpsUser).filter_by(email=email, active=True).one_or_none()
        )
        # Always verify against *something*. Short-circuiting on
        # `account is None` skips bcrypt, and the hundredfold difference in how
        # long that takes is a perfectly good answer to "does this account
        # exist" -- one a rate limit and an identical error message do nothing
        # about. `locate_store` returns "" for an address it never found, so
        # this path is reached with no account and still pays the hash.
        ok = verify_password(body.password, account.password_hash if account else _ABSENT_HASH)
        if account is None or not ok:
            attempts.record(email)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")

        attempts.clear(email)
        set_session(response, account, store=slug)
        payload = user_out(account)
        # Where the browser should go next. The client cannot work this out --
        # it does not know which store holds the address it just typed, which
        # is the whole reason one sign-in form can serve every dealership.
        return {"user": payload, "store": slug, "redirect": home_for(account, slug)}


def locate_store(email: str) -> str:
    """Which store holds this address, or "" when nothing does.

    **A URL that names a store settles it.** Signing in at
    `/alsbou/api/auth/login` searches Alsbou and nowhere else, so a Craig
    address typed there fails rather than quietly signing somebody into a
    dealership they did not ask for.

    Only an *unprefixed* sign-in searches, and then the configured default
    goes first so a single-store deployment does exactly one query and
    behaves as it always did. Two stores holding the same address would
    resolve to the first, which is a real ambiguity -- and the honest place to
    fix it is the roster, not here: one person, one dealership.

    Nothing about this is a secret channel. It reports where an account lives
    only to somebody who then has to produce its password, and a wrong address
    and a wrong password are still the same message and the same bcrypt cost.
    """
    named = current_store.get()
    if named:
        return named

    # Ours first, and once: `ops_users` is in Liner's own database rather than
    # in any store, so an owner has no store to be found in. Answering with
    # the default store is what `home_for` then turns into `/ops`.
    try:
        with ops_session() as ops:
            if ops.query(OpsUser.id).filter_by(email=email, active=True).first():
                return settings.dealership.strip()
    except OperationalError:
        pass

    default = settings.dealership.strip()
    order = [default] + [s for s in known_stores() if s != default]
    for slug in order:
        # Asked before opening, because connecting is what creates the file.
        # See `has_database` -- an unprefixed sign-in walks every profile, so
        # without this each unseeded one gained an empty database per login.
        if not has_database(slug):
            continue
        try:
            with SessionLocal(slug) as db:
                found = db.query(User.id).filter_by(email=email, active=True).first()
        except OperationalError:
            # Still caught, because the check above cannot cover every case:
            # a file that exists but holds no tables is exactly the debris
            # this used to leave, and a Postgres deployment has no path to
            # test. Without it the query fails with `no such table: users`
            # rather than returning nothing -- which came back as a 500 on
            # every sign-in, and took the constant-time guard with it: the
            # unknown-address path raised before it reached bcrypt, so an
            # unknown address answered in 6ms against a real one's 250ms.
            # That is precisely the stopwatch oracle `_ABSENT_HASH` exists to
            # close, reopened by a lookup that ran too early.
            continue
        if found:
            return slug
    return ""


def home_for(account: "User | OpsUser", slug: str) -> str:
    """The page this account's dashboard lives on.

    Ops is never prefixed: `/ops` is Liner's own and is deliberately not
    per-store, so an owner goes there whichever store's file their row was
    read from.
    """
    if isinstance(account, OpsUser):
        return "/ops"
    return f"/{slug}/app" if slug else "/app"


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
def me(request: Request, account=Depends(current_account)) -> dict:
    """Either realm -- this is the one question that has to answer for both.

    The dashboards branch on the role that comes back, so a 403 here would
    mean an ops session could not even discover it was signed in.

    It also reports the store and where this account's dashboard lives, which
    is what lets the browser send a signed-in Alsbou manager to `/alsbou/app`
    from anywhere else. The client cannot derive it: the cookie is httpOnly,
    so the page has no way to read which dealership it was minted against.
    """
    store = session_store_of(request)
    return {
        "user": user_out(account),
        "store": store,
        "home": home_for(account, store),
    }


def session_store_of(request: Request) -> str:
    """The store on this request's cookie, or the active one for a legacy cookie."""
    from app.api.deps import _session, session_store
    from app.db import active_store

    try:
        data = _session(request)
    except HTTPException:
        return active_store()
    return session_store(data) if "store" in data else active_store()
