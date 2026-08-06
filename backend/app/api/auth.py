from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import clear_session, current_user, set_session, verify_password
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


@router.post("/logout")
def logout(response: Response) -> dict:
    clear_session(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return {"user": user_out(user)}
