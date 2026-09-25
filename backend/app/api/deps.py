"""Session-cookie auth. Email + password, manager/rep roles.

No invites, no password reset, no MFA -- deliberately thin (plan §20).
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, URLSafeSerializer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, active_store, get_db, ops_session
from app.models import Dealership, OpsUser, User

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeSerializer(settings.session_secret, salt="liner-session")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd.verify(plain, hashed)


#: Which table a session's `uid` is an id in. Two tables mean an id alone is
#: ambiguous -- and an ambiguous id is one that could be looked up in the
#: wrong one. Old cookies carry no realm and are read as the dealership's,
#: which is what they were.
DEALER_REALM = "dealer"
OPS_REALM = "ops"


def set_session(response: Response, user: "User | OpsUser", store: str | None = None) -> None:
    """Sign somebody in, naming which store they signed in to.

    The store matters for the same reason the realm does, one level further
    down. With a database per dealership the cookie's `uid` is only meaningful
    in the file it was minted against -- and the signing secret is shared
    across all of them, so a cookie from Craig is *cryptographically valid* at
    `/alsbou`. Ids are UUIDs, so a lookup there would almost certainly miss
    and read as an expired session; "almost certainly" is not a guarantee
    worth resting a buyer list on, and a miss is the wrong error anyway.
    """
    response.set_cookie(
        settings.session_cookie,
        serializer.dumps({
            "uid": user.id,
            "realm": realm_of(user),
            "store": active_store() if store is None else store,
        }),
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        max_age=60 * 60 * 24 * 14,
        path="/",
    )


def session_store(data: dict) -> str:
    """Which dealership this session was minted against."""
    return str(data.get("store") or "")


def check_store(data: dict) -> None:
    """Refuse a session that belongs to a different store than this request.

    A cookie with **no** `store` key predates the split and is let through, in
    the same way a cookie with no `realm` reads as the dealership's: it can
    only have come from a deployment that served one store, and every session
    minted from now on carries the name. The window is one cookie lifetime.
    """
    if "store" not in data:
        return
    if session_store(data) != active_store():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "That session belongs to a different dealership. Sign in again here.",
        )


def clear_session(response: Response) -> None:
    response.delete_cookie(settings.session_cookie, path="/")


def realm_of(user: "User | OpsUser") -> str:
    return OPS_REALM if isinstance(user, OpsUser) else DEALER_REALM


def _session(request: Request) -> dict:
    raw = request.cookies.get(settings.session_cookie)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    try:
        return serializer.loads(raw)
    except BadSignature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session") from None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """The signed-in dealership account.

    An ops session is refused here rather than looked up: `users` and
    `ops_users` are different tables with their own id spaces, so a uid from
    one is meaningless in the other, and a lookup that happened to miss would
    read as an expired session rather than as the wrong building.
    """
    data = _session(request)
    if data.get("realm", DEALER_REALM) != DEALER_REALM:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "That account is Liner staff. The dealership's dashboard is a separate sign-in.",
        )
    check_store(data)
    user = db.query(User).filter_by(id=data.get("uid"), active=True).one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    return user


def resolve_account(db: Session, data: dict) -> "User | OpsUser | None":
    """Whoever a session names, from whichever table its realm points at.

    Two endpoints legitimately need this rather than one realm or the other:
    "who am I" and the event socket. Everything else is deliberately one-sided
    -- a dependency that quietly accepts either is how a dealership's page ends
    up rendering for one of us.
    """
    uid = data.get("uid")
    if not uid:
        return None
    if data.get("realm", DEALER_REALM) == OPS_REALM:
        # Ours, so ours to read: `ops_users` is in Liner's own database and
        # `db` here is a dealership's. Opened and closed on the spot rather
        # than taken as an argument, because every caller would otherwise have
        # to know which of the two to hand over -- and the one that guesses
        # wrong gets an empty result that reads as an expired session.
        with ops_session() as ops:
            account = ops.query(OpsUser).filter_by(id=uid, active=True).one_or_none()
            if account is not None:
                ops.expunge(account)
            return account
    return db.query(User).filter_by(id=uid, active=True).one_or_none()


def current_account(request: Request) -> "User | OpsUser":
    """Either realm, and **the store the session was minted against**.

    Only for "who am I" -- never for reading anybody's data.

    Resolved against the cookie's store rather than the request's, which is
    the one place those two should differ. Signing in unprefixed puts a Craig
    session on the browser; the very next call is `/api/auth/me` to find out
    where to go, and looking that up in the *default* store answers "Unknown
    user" -- a 401 that reads as a broken login when the sign-in had in fact
    just succeeded. Measured exactly that way before this was fixed.

    It stays safe because of what it returns: an identity and nothing else.
    Every endpoint that reads a dealership's rows goes through `current_user`,
    which checks the store matches and refuses when it does not.
    """
    data = _session(request)
    slug = session_store(data) if "store" in data else active_store()
    with SessionLocal(slug) as db:
        account = resolve_account(db, data)
        if account is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
        # Detached on purpose: the session closes here and the caller only
        # ever reads already-loaded columns off it.
        #
        # Guarded, because an ops account did not come from `db` at all --
        # `resolve_account` reads those from Liner's own database and detaches
        # them there. Expunging an object a session has never seen raises, so
        # an unguarded call here would 500 every "who am I" for an owner.
        if account in db:
            db.expunge(account)
        return account


def current_owner(request: Request) -> OpsUser:
    """The signed-in Liner account, from our own table in our own database.

    No `Depends(get_db)` any more: that resolves to a *dealership's* database,
    which no longer carries `ops_users` at all. Taking the session explicitly
    is what stops this quietly becoming a lookup in the wrong file.
    """
    data = _session(request)
    if data.get("realm", DEALER_REALM) != OPS_REALM:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Liner staff only")
    with ops_session() as ops:
        user = ops.query(OpsUser).filter_by(id=data.get("uid"), active=True).one_or_none()
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
        ops.expunge(user)
        return user


def require_manager(user: User = Depends(current_user)) -> User:
    if user.role != "manager":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Managers only")
    return user


#: Who works at the dealership. `owner` is us, and the distinction is load
#: bearing in both directions: an owner must never appear on their roster, be
#: assigned a buyer, take an appointment, or be the account a public demo
#: opens as.
DEALERSHIP_ROLES = ("manager", "rep")


def staff_query(db: Session):
    """Active dealership staff, and nobody else.

    `users` no longer holds anybody else -- we are in `ops_users` -- so the
    role filter is an assertion of that rather than a predicate doing real
    work. It stays because a row left behind by an install that predates the
    split would otherwise walk straight back onto the roster, and `make smoke`
    checks the table really is empty of them.
    """
    return db.query(User).filter(User.active.is_(True), User.role.in_(DEALERSHIP_ROLES))


def assignable_query(db: Session):
    """Staff who may take new work: `staff_query` and not `out`.

    Separate from `staff_query` because an out rep is still staff -- they stay
    on the roster and keep the buyers and appointments they already have.
    `out` only takes them out of consideration for something new.
    """
    return staff_query(db).filter(User.out.is_(False))


def find_staff(db: Session, user_id: str) -> User | None:
    """The person a lead or an appointment may be handed to, or None.

    None for an unknown id *and* for one that names an owner: from a
    dealership's side those are the same answer, and saying "that account
    exists but you may not have it" tells them we are in here.
    """
    return staff_query(db).filter(User.id == user_id).one_or_none()


def require_owner(user: OpsUser = Depends(current_owner)) -> OpsUser:
    """Liner's own people, not the dealership's.

    Their own table rather than a role on the dealership's: a role string is a
    filter every query has to remember, and the ones that forgot put us on the
    team roster, in the assignment pickers and behind the public demo door. A
    separate table cannot be queried by accident.
    """
    return user


def get_dealership(db: Session = Depends(get_db)) -> Dealership:
    dealership = db.query(Dealership).first()
    if dealership is None:
        raise HTTPException(500, "No dealership row. Run `make seed`.")
    return dealership
