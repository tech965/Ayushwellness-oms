"""Return lifecycle. Completing a return (`status -> COMPLETED`) creates
the linked `Refund` — the only Refund-creation path in Phase 1 (no live
payment gateway; spec §36 exposes Refunds as read-only via the API).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.auth import User
from app.models.enums import RefundStatus, ReturnStatus
from app.models.returns import Return
from app.repositories.order import OrderItemRepository, OrderRepository
from app.repositories.refund import RefundRepository
from app.repositories.returns import ReturnRepository
from app.schemas.common import PageParams, SortParams
from app.services.audit_service import AuditService


class ReturnService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.returns = ReturnRepository(session)
        self.orders = OrderRepository(session)
        self.order_items = OrderItemRepository(session)
        self.refunds = RefundRepository(session)
        self.audit = AuditService(session)

    async def list_returns(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        status: str | None = None,
        payment_type: str | None = None,
        customer_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[Return], int]:
        query = self.returns.search_query(
            q=q,
            status=status,
            payment_type=payment_type,
            customer_id=customer_id,
            order_id=order_id,
            date_from=date_from,
            date_to=date_to,
        )
        items, total = await self.returns.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_return(self, return_id: uuid.UUID) -> Return:
        return_ = await self.returns.get_by_id(return_id)
        if return_ is None:
            raise NotFoundError("Return not found.")
        return return_

    async def create_return(
        self,
        *,
        actor: User | None,
        order_id: uuid.UUID,
        order_item_id: uuid.UUID | None,
        customer_id: uuid.UUID | None,
        reason: str | None,
        quantity: int,
    ) -> Return:
        order = await self.orders.get_by_id(order_id)
        if order is None:
            raise NotFoundError("Order not found.")

        return_ = await self.returns.create(
            order_id=order_id,
            order_item_id=order_item_id,
            customer_id=customer_id,
            reason=reason,
            quantity=quantity,
            status=ReturnStatus.REQUESTED,
            requested_at=datetime.now(UTC),
            source_system="manual",
        )
        await self.audit.record(
            user=actor,
            action="return.created",
            entity_type="return",
            entity_id=str(return_.id),
            new_value={"order_id": str(order_id), "quantity": quantity},
        )
        await self.session.commit()
        return return_

    async def update_return(
        self,
        return_id: uuid.UUID,
        *,
        actor: User | None,
        status: ReturnStatus | None,
        notes: str | None,
    ) -> Return:
        return_ = await self.get_return(return_id)
        clean: dict = {}
        if notes is not None:
            clean["notes"] = notes
        if status is not None and status != return_.status:
            clean["status"] = status
            clean[self._timestamp_field_for(status)] = datetime.now(UTC)

        if clean:
            await self.returns.update(return_, **clean)

        if status == ReturnStatus.COMPLETED:
            await self._create_refund_for_return(return_, actor=actor)

        await self.session.commit()
        return return_

    def _timestamp_field_for(self, status: ReturnStatus) -> str:
        return {
            ReturnStatus.APPROVED: "approved_at",
            ReturnStatus.RECEIVED: "received_at",
            ReturnStatus.COMPLETED: "completed_at",
        }.get(status, "notes")

    async def _create_refund_for_return(self, return_: Return, *, actor: User | None) -> None:
        if await self.refunds.get_by_return_id(return_.id) is not None:
            return  # already refunded — idempotent

        amount = await self._compute_refund_amount(return_)
        refund = await self.refunds.create(
            order_id=return_.order_id,
            return_id=return_.id,
            amount=amount,
            reason=return_.reason,
            status=RefundStatus.PENDING,
            initiated_at=datetime.now(UTC),
            source_system="manual",
        )
        await self.audit.record(
            user=actor,
            action="refund.created",
            entity_type="refund",
            entity_id=str(refund.id),
            new_value={"order_id": str(return_.order_id), "amount": str(amount)},
        )

    async def _compute_refund_amount(self, return_: Return) -> Decimal:
        if return_.order_item_id:
            item = await self.order_items.get_by_id(return_.order_item_id)
            if item and item.quantity:
                per_unit = item.total_amount / item.quantity
                return (per_unit * return_.quantity).quantize(Decimal("0.01"))

        order = await self.orders.get_by_id(return_.order_id)
        return order.total_amount if order else Decimal("0")
