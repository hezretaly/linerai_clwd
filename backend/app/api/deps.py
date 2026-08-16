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
from app.models import Dealership, User

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeSerializer(settings.session_secret, salt="liner-session")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd.verify(plain, hashed)


def set_session(response: Response, user: User) -> None:
    response.set_cookie(
        settings.session_cookie,
        serializer.dumps({"uid": user.id}),
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        max_age=60 * 60 * 24 * 14,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(settings.session_cookie, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    raw = request.cookies.get(settings.session_cookie)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    try:
        data = serializer.loads(raw)
    except BadSignature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session") from None

    user = db.query(User).filter_by(id=data.get("uid"), active=True).one_or_none()
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

    One predicate rather than a `role != 'owner'` at each of the five call
    sites -- the roster, three assignment paths and the public door. Adding
    `owner` to the users table made every unfiltered `query(User)` a place our
    own accounts could surface inside somebody else's showroom, and the copy
    that gets missed is the one nobody notices.
    """
    return db.query(User).filter(User.active.is_(True), User.role.in_(DEALERSHIP_ROLES))


def find_staff(db: Session, user_id: str) -> User | None:
    """The person a lead or an appointment may be handed to, or None.

    None for an unknown id *and* for one that names an owner: from a
    dealership's side those are the same answer, and saying "that account
    exists but you may not have it" tells them we are in here.
    """
    return staff_query(db).filter(User.id == user_id).one_or_none()


def require_owner(user: User = Depends(current_user)) -> User:
    """Liner's own people, not the dealership's.

    A third role rather than reusing `manager`: a manager runs a showroom and
    has every reason to read its buyer list, which is exactly what these two
    have no business reading. The split runs the other way too -- nothing under
    /api/ops is open to a dealership's staff, however senior.
    """
    if user.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Liner staff only")
    return user


def get_dealership(db: Session = Depends(get_db)) -> Dealership:
    dealership = db.query(Dealership).first()
    if dealership is None:
        raise HTTPException(500, "No dealership row. Run `make seed`.")
    return dealership
