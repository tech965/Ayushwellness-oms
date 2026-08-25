"""Generic `entity_type -> OMS service upsert` dispatch.

Shared by `app.services.sync_service.SyncService.execute_sync`'s
fetch/normalize/upsert loop and `app.tasks.webhook_processing`, so
neither has to hardcode which OMS service owns which entity type more
than once. This is the one place in the integrations layer that imports
OMS domain services — adapters themselves never do (see
`docs/architecture/integrations.md#why-the-oms-core-must-not-import-a-provider-sdk`,
which is also why this dependency only points one direction).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.customer_service import CustomerService
from app.services.ndr_service import NDRService
from app.services.order_service import OrderService
from app.services.product_service import ProductService

UpsertHandler = Callable[[AsyncSession, dict[str, Any]], Awaitable[tuple[Any, bool]]]


async def _upsert_customer(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await CustomerService(session).upsert_synced_customer(**data)


async def _upsert_product(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await ProductService(session).upsert_synced_product(**data)


async def _upsert_order(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await OrderService(session).upsert_synced_order(**data)


async def _upsert_ndr(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await NDRService(session).upsert_synced_ndr(**data)


ENTITY_UPSERT_HANDLERS: dict[str, UpsertHandler] = {
    "customers": _upsert_customer,
    "products": _upsert_product,
    "orders": _upsert_order,
    "ndr": _upsert_ndr,
}
