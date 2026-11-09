"""Live (API-first) order listing: Shopify order content + OMS workflow.

Used by `GET /orders` when `settings.SHOPIFY_LIVE_MODE` is on. Order
content (amounts, items, addresses, payment, fulfilment, tags) comes from
Shopify at request time and is never written anywhere. OMS-owned state
(status, notes, telecaller confirmation) is read from `order_workflow` and
merged in memory. The only database access here is READS.

Two query paths:

* Workflow-driven -- when a filter can only be answered from OMS state
  (`confirmed_only`, `telecaller_id`, confirmed date range): the matching
  external ids are paged in SQL (exact total), then fetched live by id.
* Shopify-driven -- everything else: Shopify's cursor API is crawled
  (search text and created-date range are pushed down to Shopify's own
  search), remaining filters are applied in memory, and offset pagination
  is emulated by crawling to `offset + page_size`. The crawl is bounded;
  a truncated or estimated total is reported, never presented as exact.

Filters that depend on Shiprocket or local customer rows
(`shipment_status`, `courier_id`, `customer_id`) are rejected with a
clear error in live mode rather than silently ignored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.integrations.shopify.live import MAX_PAGE_SIZE, ShopifyLiveReader
from app.models.auth import User
from app.models.enums import CancellationStatus, OrderStatus
from app.models.order_workflow import OrderWorkflow
from app.schemas.common import PageParams
from app.schemas.order import OrderListResponse
from app.services.order_ref import get_workflow_by_external_ids, ref_id_for

# Upper bound on Shopify pages crawled for one list request.
MAX_CRAWL_PAGES = 20

UNSUPPORTED_IN_LIVE_MODE = ("shipment_status", "courier_id", "customer_id")


@dataclass
class LiveOrderList:
    items: list[OrderListResponse]
    total_items: int
    total_pages: int
    has_more: bool
    total_is_estimate: bool
    warnings: list[str] = field(default_factory=list)


def _reject_unsupported(filters: dict[str, Any]) -> None:
    used = [name for name in UNSUPPORTED_IN_LIVE_MODE if filters.get(name) is not None]
    if used:
        raise ValidationError(
            "These filters are not available in live-data mode "
            f"(they depend on Shiprocket/local customer data): {', '.join(used)}."
        )


def _effective_status(order: dict[str, Any], workflow: OrderWorkflow | None) -> OrderStatus:
    if order.get("is_cancelled"):
        return OrderStatus.CANCELLED
    return workflow.status if workflow is not None else OrderStatus.PENDING


def _matches(
    order: dict[str, Any],
    workflow: OrderWorkflow | None,
    *,
    status: OrderStatus | None,
    payment_status: Any,
    payment_type: Any,
    fulfillment_status: Any,
    sku: str | None,
    tag: str | None,
    amount_min: Decimal | None,
    amount_max: Decimal | None,
) -> bool:
    if status is not None and _effective_status(order, workflow) != status:
        return False
    if payment_status is not None and order["payment_status"] != payment_status:
        return False
    if payment_type is not None and order["payment_type"] != payment_type:
        return False
    if fulfillment_status is not None and order["fulfillment_status"] != fulfillment_status:
        return False
    if amount_min is not None and order["total_amount"] < amount_min:
        return False
    if amount_max is not None and order["total_amount"] > amount_max:
        return False
    if sku and not any(sku.lower() in (i.get("sku") or "").lower() for i in order["items"]):
        return False
    if tag and tag not in (order.get("shopify_tags") or []):
        return False
    return True


def _address_contact(order: dict[str, Any]) -> tuple[str | None, str | None]:
    address = order.get("shipping_address") or order.get("billing_address") or {}
    return address.get("name") or address.get("contact_name"), (
        address.get("phone") or address.get("contact_phone")
    )


def to_list_response(
    order: dict[str, Any],
    workflow: OrderWorkflow | None,
    *,
    confirmed_by_name: str | None,
) -> OrderListResponse:
    external_id = order["external_id"]
    items = order["items"]
    summary: str | None = None
    if items:
        first = items[0]["product_name"]
        summary = first if len(items) == 1 else f"{first} +{len(items) - 1} more"
    name, phone = _address_contact(order)
    created = order["external_created_at"] or order["order_datetime"]
    updated = order["external_updated_at"] or created
    cancelled = bool(order.get("is_cancelled"))

    return OrderListResponse(
        id=ref_id_for(external_id, workflow),
        order_number=order["order_number"],
        shopify_order_id=external_id,
        customer_id=None,
        order_datetime=order["order_datetime"],
        currency=order["currency"],
        subtotal=order["subtotal"],
        discount_amount=order["discount_amount"],
        tax_amount=order["tax_amount"],
        shipping_charge=order["shipping_charge"],
        total_amount=order["total_amount"],
        payment_type=order["payment_type"],
        payment_status=order["payment_status"],
        status=_effective_status(order, workflow),
        fulfillment_status=order["fulfillment_status"],
        cancellation_status=CancellationStatus.CANCELLED if cancelled else CancellationStatus.NONE,
        notes=workflow.notes if workflow else None,
        shopify_tags=order.get("shopify_tags"),
        shopify_order_note=order.get("shopify_order_note"),
        shopify_shipment_status=order.get("shopify_shipment_status"),
        shipping_address=order.get("shipping_address"),
        billing_address=order.get("billing_address"),
        source_system="shopify",
        confirmed_by_telecaller_id=workflow.confirmed_by_telecaller_id if workflow else None,
        confirmed_at=workflow.confirmed_at if workflow else None,
        shopify_confirmation_sync_status=(
            workflow.shopify_confirmation_sync_status.value if workflow else None
        ),
        shopify_confirmation_sync_error=(
            workflow.shopify_confirmation_sync_error if workflow else None
        ),
        created_at=created,
        updated_at=updated,
        customer_name=name,
        customer_phone=phone,
        customer_email=order.get("customer_email"),
        item_summary=summary,
        total_quantity=sum(i["quantity"] for i in items),
        confirmed_by_telecaller_name=confirmed_by_name,
    )


class LiveOrderService:
    def __init__(self, session: AsyncSession, reader: ShopifyLiveReader) -> None:
        self.session = session
        self.reader = reader

    async def _telecaller_names(self, workflows: dict[str, OrderWorkflow]) -> dict[Any, str]:
        ids = {
            w.confirmed_by_telecaller_id for w in workflows.values() if w.confirmed_by_telecaller_id
        }
        if not ids:
            return {}
        rows = (
            await self.session.execute(select(User.id, User.name).where(User.id.in_(ids)))
        ).all()
        return {uid: name for uid, name in rows}

    async def _finish(self, orders: list[dict[str, Any]]) -> list[OrderListResponse]:
        workflows = await get_workflow_by_external_ids(
            self.session, [o["external_id"] for o in orders]
        )
        names = await self._telecaller_names(workflows)
        out: list[OrderListResponse] = []
        for order in orders:
            wf = workflows.get(order["external_id"])
            name = names.get(wf.confirmed_by_telecaller_id) if wf else None
            out.append(to_list_response(order, wf, confirmed_by_name=name))
        await self._attach_shipments(out)
        return out

    async def _attach_shipments(self, rows: list[OrderListResponse]) -> None:
        """Shipment columns, read live from Shiprocket for the orders on
        this page that have a shipment reference (one query + bounded
        concurrent tracking calls). A failed lookup is shown as
        'unavailable', never as a guessed status."""
        if not rows:
            return
        from app.models.shipment_ref import ShipmentRef
        from app.services.live_shiprocket import LiveShiprocketService

        refs = (
            (
                await self.session.execute(
                    select(ShipmentRef)
                    .where(ShipmentRef.order_id.in_([r.id for r in rows]))
                    .order_by(ShipmentRef.created_at)
                )
            )
            .scalars()
            .all()
        )
        latest = {ref.order_id: ref for ref in refs}
        if not latest:
            return
        service = LiveShiprocketService(self.session)
        chosen = list(latest.values())
        try:
            states = await service._states(chosen)  # noqa: SLF001
        except Exception:  # noqa: BLE001 - degrade to 'unavailable', never guess
            states = [None] * len(chosen)  # type: ignore[list-item]
        for ref, state in zip(chosen, states, strict=True):
            row = next(r for r in rows if r.id == ref.order_id)
            row.shipment_id = ref.id
            row.tracking_number = ref.awb
            row.shiprocket_order_id = ref.shiprocket_order_id
            row.shopify_sync_status = ref.shopify_sync_status.value
            row.shipment_status = state.status.value if state else "unavailable"
            row.courier_name = state.courier_name if state else None

    async def list_orders(
        self,
        *,
        page_params: PageParams,
        q: str | None = None,
        status: OrderStatus | None = None,
        payment_status: Any = None,
        payment_type: Any = None,
        fulfillment_status: Any = None,
        shipment_status: Any = None,
        courier_id: Any = None,
        sku: str | None = None,
        tag: str | None = None,
        amount_min: Decimal | None = None,
        amount_max: Decimal | None = None,
        customer_id: Any = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        confirmed_only: bool = False,
        telecaller_id: Any = None,
        confirmed_date_from: datetime | None = None,
        confirmed_date_to: datetime | None = None,
    ) -> LiveOrderList:
        _reject_unsupported(
            {
                "shipment_status": shipment_status,
                "courier_id": courier_id,
                "customer_id": customer_id,
            }
        )
        client_filters = {
            "status": status,
            "payment_status": payment_status,
            "payment_type": payment_type,
            "fulfillment_status": fulfillment_status,
            "sku": sku,
            "tag": tag,
            "amount_min": amount_min,
            "amount_max": amount_max,
        }
        workflow_driven = bool(
            confirmed_only or telecaller_id or confirmed_date_from or confirmed_date_to
        )
        if workflow_driven:
            return await self._list_workflow_driven(
                page_params=page_params,
                q=q,
                date_from=date_from,
                date_to=date_to,
                confirmed_only=confirmed_only,
                telecaller_id=telecaller_id,
                confirmed_date_from=confirmed_date_from,
                confirmed_date_to=confirmed_date_to,
                client_filters=client_filters,
            )
        return await self._list_shopify_driven(
            page_params=page_params,
            q=q,
            date_from=date_from,
            date_to=date_to,
            client_filters=client_filters,
        )

    # --- Shopify-driven ------------------------------------------------------

    async def _list_shopify_driven(
        self,
        *,
        page_params: PageParams,
        q: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        client_filters: dict[str, Any],
    ) -> LiveOrderList:
        offset = (page_params.page - 1) * page_params.page_size
        need = offset + page_params.page_size + 1  # +1 detects "more"
        needs_workflow = client_filters["status"] is not None
        matched: list[dict[str, Any]] = []
        cursor: str | None = None
        has_more = True
        pages = 0
        warnings: list[str] = []

        while len(matched) < need and has_more and pages < MAX_CRAWL_PAGES:
            page = await self.reader.list_orders(
                limit=MAX_PAGE_SIZE, cursor=cursor, q=q, date_from=date_from, date_to=date_to
            )
            pages += 1
            cursor, has_more = page.next_cursor, page.has_more
            workflows = (
                await get_workflow_by_external_ids(
                    self.session, [o["external_id"] for o in page.items]
                )
                if needs_workflow
                else {}
            )
            for order in page.items:
                if _matches(order, workflows.get(order["external_id"]), **client_filters):
                    matched.append(order)

        truncated = has_more and len(matched) < need
        if truncated:
            warnings.append(
                f"Stopped after {MAX_CRAWL_PAGES} Shopify pages; results are incomplete. "
                "Narrow the date range or search."
            )
        page_orders = matched[offset : offset + page_params.page_size]
        more = len(matched) > offset + page_params.page_size or truncated
        total = len(matched) if len(matched) < need else offset + page_params.page_size + 1
        return LiveOrderList(
            items=await self._finish(page_orders),
            total_items=total,
            total_pages=max(1, math.ceil(total / page_params.page_size)),
            has_more=more,
            total_is_estimate=more or truncated,
            warnings=warnings,
        )

    # --- Workflow-driven -----------------------------------------------------

    async def _list_workflow_driven(
        self,
        *,
        page_params: PageParams,
        q: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        confirmed_only: bool,
        telecaller_id: Any,
        confirmed_date_from: datetime | None,
        confirmed_date_to: datetime | None,
        client_filters: dict[str, Any],
    ) -> LiveOrderList:
        conditions: list[Any] = []
        if confirmed_only:
            conditions.append(OrderWorkflow.confirmed_by_telecaller_id.is_not(None))
        if telecaller_id:
            conditions.append(OrderWorkflow.confirmed_by_telecaller_id == telecaller_id)
        if confirmed_date_from:
            conditions.append(OrderWorkflow.confirmed_at >= confirmed_date_from)
        if confirmed_date_to:
            conditions.append(OrderWorkflow.confirmed_at <= confirmed_date_to)

        total = (
            await self.session.execute(
                select(func.count()).select_from(OrderWorkflow).where(*conditions)
            )
        ).scalar_one()
        offset = (page_params.page - 1) * page_params.page_size
        rows = (
            (
                await self.session.execute(
                    select(OrderWorkflow)
                    .where(*conditions)
                    .order_by(OrderWorkflow.confirmed_at.desc().nulls_last(), OrderWorkflow.id)
                    .offset(offset)
                    .limit(page_params.page_size)
                )
            )
            .scalars()
            .all()
        )
        wanted = [w.external_order_id for w in rows]
        live = await self.reader.get_orders_by_ids(
            wanted, q=q, date_from=date_from, date_to=date_to
        )
        by_id = {o["external_id"]: o for o in live}
        workflows = {w.external_order_id: w for w in rows}

        ordered = [by_id[i] for i in wanted if i in by_id]
        kept = [
            o for o in ordered if _matches(o, workflows.get(o["external_id"]), **client_filters)
        ]
        warnings: list[str] = []
        missing = len(wanted) - len(ordered)
        if missing and not (q or date_from or date_to):
            warnings.append(
                f"{missing} order(s) with workflow state could not be loaded from Shopify."
            )
        narrowed = bool(q or date_from or date_to) or any(
            v is not None for v in client_filters.values()
        )
        return LiveOrderList(
            items=await self._finish(kept),
            total_items=total,
            total_pages=max(1, math.ceil(total / page_params.page_size)),
            has_more=offset + len(rows) < total,
            total_is_estimate=narrowed,
            warnings=warnings,
        )
