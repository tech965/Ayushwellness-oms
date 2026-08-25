"""AuditLog — OMS-owned, never externally sourced.

Append-only: no update/delete repository methods. Never stores passwords,
tokens, or secrets — see `app.core.logging._redact_sensitive` for the
same rule applied to structured logs, and `app.services.audit_service`
for the write path.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.auth import User


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_logs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    previous_value: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audit_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False, index=True
    )

    user: Mapped[User | None] = relationship()
