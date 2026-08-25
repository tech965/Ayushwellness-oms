from __future__ import annotations

import uuid

from sqlalchemy import Select, select

from app.models.integration import WebhookEvent
from app.repositories.base import BaseRepository


class WebhookEventRepository(BaseRepository[WebhookEvent]):
    model = WebhookEvent

    def for_integration(self, integration_id: uuid.UUID) -> Select:
        return select(WebhookEvent).where(WebhookEvent.integration_id == integration_id)

    async def get_by_integration_and_external_event_id(
        self, *, integration_id: uuid.UUID, external_event_id: str
    ) -> WebhookEvent | None:
        stmt = select(WebhookEvent).where(
            WebhookEvent.integration_id == integration_id,
            WebhookEvent.external_event_id == external_event_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
