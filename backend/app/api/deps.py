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


def get_dealership(db: Session = Depends(get_db)) -> Dealership:
    dealership = db.query(Dealership).first()
    if dealership is None:
        raise HTTPException(500, "No dealership row. Run `make seed`.")
    return dealership
