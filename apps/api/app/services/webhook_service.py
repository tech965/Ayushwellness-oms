"""Generic webhook ingestion — provider-agnostic idempotency + status
tracking (spec §6/§7). Provider-specific signature verification and
per-event processing logic are Phase 2 (`app.integrations.<provider>.WebhookHandler`);
this service only guarantees that the same event, delivered twice, is
recorded once.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.enums import WebhookEventStatus
from app.models.integration import WebhookEvent
from app.repositories.integration import IntegrationRepository
from app.repositories.webhook_event import WebhookEventRepository
from app.schemas.common import PageParams, SortParams


def compute_fallback_event_id(
    *, integration_code: str, event_type: str, payload: dict[str, Any]
) -> str:
    """Deterministic id for providers that don't supply a stable event id:
    the same (integration, event_type, payload) always hashes to the same
    value, so a retried delivery still collides on the unique constraint
    instead of creating a duplicate `WebhookEvent`.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{integration_code}:{event_type}:{canonical}".encode()).hexdigest()
    return f"fallback:{digest}"


class WebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.integrations = IntegrationRepository(session)
        self.webhook_events = WebhookEventRepository(session)

    async def ingest(
        self,
        *,
        integration_id: uuid.UUID,
        event_type: str,
        payload: dict[str, Any],
        external_event_id: str | None = None,
        external_resource_id: str | None = None,
    ) -> tuple[WebhookEvent, bool]:
        """Returns (event, created). `created=False` means this exact event
        was already recorded — the caller must treat it as a no-op, never
        re-process it.
        """
        integration = await self.integrations.get_by_id(integration_id)
        if integration is None:
            raise NotFoundError("Integration not found.")

        event_id = external_event_id or compute_fallback_event_id(
            integration_code=integration.code, event_type=event_type, payload=payload
        )

        existing = await self.webhook_events.get_by_integration_and_external_event_id(
            integration_id=integration_id, external_event_id=event_id
        )
        if existing is not None:
            return existing, False

        event = await self.webhook_events.create(
            integration_id=integration_id,
            event_type=event_type,
            external_event_id=event_id,
            external_resource_id=external_resource_id,
            received_at=datetime.now(UTC),
            status=WebhookEventStatus.RECEIVED,
            payload=payload,
        )
        await self.session.commit()
        return event, True

    async def _get(self, webhook_event_id: uuid.UUID) -> WebhookEvent:
        event = await self.webhook_events.get_by_id(webhook_event_id)
        if event is None:
            raise NotFoundError("Webhook event not found.")
        return event

    async def mark_processing(self, webhook_event_id: uuid.UUID) -> WebhookEvent:
        event = await self._get(webhook_event_id)
        await self.webhook_events.update(event, status=WebhookEventStatus.PROCESSING)
        await self.session.commit()
        return event

    async def mark_processed(self, webhook_event_id: uuid.UUID) -> WebhookEvent:
        event = await self._get(webhook_event_id)
        await self.webhook_events.update(
            event, status=WebhookEventStatus.PROCESSED, processed_at=datetime.now(UTC)
        )
        await self.session.commit()
        return event

    async def mark_failed(self, webhook_event_id: uuid.UUID, *, error_message: str) -> WebhookEvent:
        event = await self._get(webhook_event_id)
        await self.webhook_events.update(
            event,
            status=WebhookEventStatus.FAILED,
            # `error_message` is `String(1000)` -- a raw `str(exc)` from a
            # SQLAlchemy/IntegrityError (full statement + bound params +
            # driver text) routinely exceeds that, and writing it
            # untruncated raises a *second*, unrelated
            # StringDataRightTruncationError that masks the real failure
            # and can itself go uncaught (the same class of bug already
            # fixed once for `SyncError.error_message` -- see
            # `app.services.sync_service.SyncService.record_error`). The
            # full, untruncated message belongs in structured logs
            # (callers log it before calling this), never silently lost,
            # just not persisted in a column too small to hold it.
            error_message=error_message[:1000],
            retry_count=event.retry_count + 1,
        )
        await self.session.commit()
        return event

    async def mark_ignored(self, webhook_event_id: uuid.UUID, *, reason: str) -> WebhookEvent:
        event = await self._get(webhook_event_id)
        await self.webhook_events.update(
            event, status=WebhookEventStatus.IGNORED, error_message=reason[:1000]
        )
        await self.session.commit()
        return event

    async def list_events(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        integration_id: uuid.UUID | None = None,
        external_resource_id: str | None = None,
    ) -> tuple[list[WebhookEvent], int]:
        """`external_resource_id` is optional and additive — every
        existing caller that only ever passed `integration_id` (or
        neither) keeps its exact prior behavior unchanged.
        """
        query = (
            self.webhook_events.search_query(
                integration_id=integration_id, external_resource_id=external_resource_id
            )
            if integration_id is not None or external_resource_id is not None
            else None
        )
        items, total = await self.webhook_events.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_event(self, webhook_event_id: uuid.UUID) -> WebhookEvent:
        return await self._get(webhook_event_id)
