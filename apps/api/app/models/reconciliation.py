"""ReconciliationRun, ReconciliationResult — Phase 2.4.

OMS-owned, never externally sourced (no `SyncMetadataMixin` — these track
the reconciliation *process*, not a synced business entity, same
distinction `Integration`/`SyncJob`/`SyncError` already draw for sync).
Reconciliation only ever reports mismatches (`ReconciliationService`
never writes to a business table) — see
`docs/architecture/integrations.md#reconciliation`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ReconciliationRunStatus, ReconciliationStatus, sa_enum


class ReconciliationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "reconciliation_runs"

    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ReconciliationRunStatus] = mapped_column(
        sa_enum(ReconciliationRunStatus, "reconciliation_run_status"),
        nullable=False,
        default=ReconciliationRunStatus.RUNNING,
        server_default=ReconciliationRunStatus.RUNNING.value,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    total_checked: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reconciled_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    mismatch_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    missing_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Which checks ran vs. were skipped (e.g. "shopify not configured") —
    # never fabricated; mirrors the health-check honesty rule.
    run_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    results: Mapped[list[ReconciliationResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ReconciliationResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "reconciliation_results"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "check_type",
            "internal_id",
            "external_id",
            name="uq_reconciliation_results_run_check_entity",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    check_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    internal_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    expected_value: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    actual_value: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[ReconciliationStatus] = mapped_column(
        sa_enum(ReconciliationStatus, "reconciliation_status"), nullable=False, index=True
    )
    message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    run: Mapped[ReconciliationRun] = relationship(back_populates="results")
