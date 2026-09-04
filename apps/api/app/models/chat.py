"""ChatQueryLog — one row per answered (or failed) AI-assistant question.

Append-only, same discipline as `AuditLog`: no update/delete path. Used
for internal audit and quality review of the OMS AI Assistant. Stores the
question and the assistant's answer text plus which tools/sources were
touched, success/failure, and latency — never tokens, credentials, or raw
customer PII beyond whatever the user typed into their own question.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.auth import User


class ChatQueryLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "chat_query_logs"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)

    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    tools_used: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False, index=True
    )

    user: Mapped[User | None] = relationship()
