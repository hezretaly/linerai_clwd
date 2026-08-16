"""Session-cookie auth. Email + password, manager/rep roles.

No invites, no password reset, no MFA -- deliberately thin (plan §20).
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, URLSafeSerializer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
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


def set_session(response: Response, user: "User | OpsUser") -> None:
    response.set_cookie(
        settings.session_cookie,
        serializer.dumps({"uid": user.id, "realm": realm_of(user)}),
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        max_age=60 * 60 * 24 * 14,
        path="/",
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
        return db.query(OpsUser).filter_by(id=uid, active=True).one_or_none()
    return db.query(User).filter_by(id=uid, active=True).one_or_none()


def current_account(request: Request, db: Session = Depends(get_db)) -> "User | OpsUser":
    """Either realm. Only for "who am I" -- never for reading anybody's data."""
    account = resolve_account(db, _session(request))
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    return account


def current_owner(request: Request, db: Session = Depends(get_db)) -> OpsUser:
    """The signed-in Liner account, from our own table."""
    data = _session(request)
    if data.get("realm", DEALER_REALM) != OPS_REALM:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Liner staff only")
    user = db.query(OpsUser).filter_by(id=data.get("uid"), active=True).one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
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
