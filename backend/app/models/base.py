from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import utcnow


def new_id() -> str:
    return str(uuid.uuid4())


def pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_id)


def created() -> Mapped[datetime]:
    return mapped_column(DateTime, default=utcnow, nullable=False)
