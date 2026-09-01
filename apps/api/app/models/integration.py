"""Integration, SyncJob, SyncError, WebhookEvent.

The generic infrastructure Shopify/Shiprocket/courier adapters will
synchronize through starting Phase 2 — see `docs/architecture/integrations.md`.
No credential is ever stored on `Integration`; secrets are resolved at
call time via `app.integrations.credentials` and `configuration` holds
only non-secret metadata (store domain, sync cadence, entity filters, ...).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    IntegrationStatus,
    IntegrationType,
    SyncJobStatus,
    SyncType,
    WebhookEventStatus,
    sa_enum,
)


class IntegrationCode:
    """Known integration codes. Plain strings, not a DB enum — matches
    `app.models.mixins.SourceSystem`: a new provider is onboarded by
    adding a row, never a migration.
    """

    SHOPIFY = "shopify"
    SHIPROCKET = "shiprocket"
    BLUE_DART = "blue_dart"
    DELHIVERY = "delhivery"
    ECOM_EXPRESS = "ecom_express"
    WHATSAPP = "whatsapp"
    META = "meta"
    INSTAGRAM = "instagram"
    CASHFREE = "cashfree"


class Integration(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "integrations"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    type: Mapped[IntegrationType] = mapped_column(
        sa_enum(IntegrationType, "integration_type"), nullable=False
    )
    status: Mapped[IntegrationStatus] = mapped_column(
        sa_enum(IntegrationStatus, "integration_status"),
        nullable=False,
        default=IntegrationStatus.DISCONNECTED,
        server_default=IntegrationStatus.DISCONNECTED.value,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    configuration: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    last_failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    sync_jobs: Mapped[list[SyncJob]] = relationship(
        back_populates="integration", cascade="all, delete-orphan"
    )
    webhook_events: Mapped[list[WebhookEvent]] = relationship(
        back_populates="integration", cascade="all, delete-orphan"
    )


class SyncJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sync_jobs"
    __table_args__ = (
        # Defense-in-depth for "at most one active (QUEUED/RUNNING) sync
        # per (integration, entity_type)" — `SyncService.start_sync`
        # enforces this with a check-then-create, which has a genuine
        # race window between two near-simultaneous trigger requests; this
        # is the DB-level backstop that makes the second insert fail
        # instead of silently creating two concurrent jobs. Mirrors
        # `OrderAssignment`'s identical "one active per X" partial index.
        Index(
            "uq_sync_jobs_one_active_per_entity",
            "integration_id",
            "entity_type",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sync_type: Mapped[SyncType] = mapped_column(sa_enum(SyncType, "sync_type"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[SyncJobStatus] = mapped_column(
        sa_enum(SyncJobStatus, "sync_job_status"),
        nullable=False,
        default=SyncJobStatus.QUEUED,
        server_default=SyncJobStatus.QUEUED.value,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    records_received: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    records_created: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    records_updated: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    records_skipped: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    records_failed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    job_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)

    integration: Mapped[Integration] = relationship(back_populates="sync_jobs")
    errors: Mapped[list[SyncError]] = relationship(
        back_populates="sync_job", cascade="all, delete-orphan"
    )


class SyncError(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sync_errors"

    sync_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sync_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Never the raw payload itself (may contain PII/secrets) — a pointer
    # (e.g. a WebhookEvent id, or a provider request id) for later lookup.
    error_message: Mapped[str] = mapped_column(String(1000), nullable=False)
    payload_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    sync_job: Mapped[SyncJob] = relationship(back_populates="errors")


class WebhookEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """`external_event_id` is always populated — either the provider's own
    event id, or (when a provider doesn't supply a stable one) a
    deterministic fallback hash computed by
    `app.services.webhook_service.compute_fallback_event_id` over
    (integration, event_type, payload), so the same
    (integration_id, external_event_id) uniqueness rule provides
    idempotency in both cases.
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "integration_id", "external_event_id", name="uq_webhook_events_integration_external"
        ),
    )

    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    received_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    status: Mapped[WebhookEventStatus] = mapped_column(
        sa_enum(WebhookEventStatus, "webhook_event_status"),
        nullable=False,
        default=WebhookEventStatus.RECEIVED,
        server_default=WebhookEventStatus.RECEIVED.value,
        index=True,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    integration: Mapped[Integration] = relationship(back_populates="webhook_events")
