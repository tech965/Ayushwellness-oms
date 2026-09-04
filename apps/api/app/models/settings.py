"""AppSettings — one organization-wide settings row (Phase: OMS Settings).

Deliberately a single JSON blob (`values`) behind a typed Pydantic schema
at the API layer (`app/schemas/settings.py`), not one column per setting:
the settings surface (general/orders/notifications/shipping/dashboard/
security preferences) is expected to keep growing, and a JSON blob means
adding a field is a code-only change, not another Alembic migration --
the same pattern `Integration.configuration`/`Refund.refund_metadata`
already use elsewhere in this codebase for open-ended, non-relational
data. Single-tenant: exactly one row is ever created (see
`SettingsRepository.get_or_create`), same assumption the rest of this OMS
already makes (one organization, no multi-tenant scoping anywhere).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.auth import User


class AppSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "app_settings"

    values: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    updated_by: Mapped[User | None] = relationship()
