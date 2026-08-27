from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, select

from app.models.enums import WebhookEventStatus
from app.models.integration import WebhookEvent
from app.repositories.base import BaseRepository


class WebhookEventRepository(BaseRepository[WebhookEvent]):
    model = WebhookEvent

    def for_integration(self, integration_id: uuid.UUID) -> Select:
        return select(WebhookEvent).where(WebhookEvent.integration_id == integration_id)

    async def get_stuck_received(self, *, received_before: datetime) -> list[WebhookEvent]:
        """Round 4 fix: `receive_shopify_webhook` persists the
        `WebhookEvent` row *before* attempting `process_webhook_event_task.delay()`,
        and deliberately swallows a broker-enqueue failure so a broker
        outage never fails the webhook ack (see that endpoint's
        docstring) — but nothing was re-driving those events afterward,
        so one that failed to enqueue stayed at `RECEIVED` forever even
        after the broker recovered. `received_before` should be a few
        minutes in the past so an event still legitimately waiting for
        its worker isn't mistaken for a stuck one.
        """
        stmt = select(WebhookEvent).where(
            WebhookEvent.status == WebhookEventStatus.RECEIVED,
            WebhookEvent.received_at < received_before,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_integration_and_external_event_id(
        self, *, integration_id: uuid.UUID, external_event_id: str
    ) -> WebhookEvent | None:
        stmt = select(WebhookEvent).where(
            WebhookEvent.integration_id == integration_id,
            WebhookEvent.external_event_id == external_event_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
