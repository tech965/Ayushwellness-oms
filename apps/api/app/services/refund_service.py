"""Read-only in Phase 1. Refund rows are created by `ReturnService` when
a return completes — see that module's docstring.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.refund import Refund
from app.repositories.refund import RefundRepository
from app.schemas.common import PageParams, SortParams


class RefundService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.refunds = RefundRepository(session)

    async def list_refunds(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        status: str | None = None,
        payment_type: str | None = None,
        order_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[Refund], int]:
        query = self.refunds.search_query(
            q=q,
            status=status,
            payment_type=payment_type,
            order_id=order_id,
            date_from=date_from,
            date_to=date_to,
        )
        items, total = await self.refunds.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_refund(self, refund_id: uuid.UUID) -> Refund:
        refund = await self.refunds.get_by_id(refund_id)
        if refund is None:
            raise NotFoundError("Refund not found.")
        return refund
