"""Shipment Staff: a dedicated, SCOPED shipment-processing experience —
the `/shipment-staff/*` counterpart to `TelecallingService`'s
`/telecaller/*`. Every method here follows the exact same rule
`TelecallingService`'s own module docstring states for its scope
argument: the permitted scope is always derived from the authenticated
`actor` (`resolve_scope`), never from a client-supplied id, so a
Shipment Staff user changing an order/shipment id in the URL only ever
resolves a row actually within their scope — anything else raises the
same `AuthorizationError` (403) `TelecallingService.get_scoped_assignment`
already uses, and for the identical reason (see its docstring): doesn't
distinguish "doesn't exist" from "exists but isn't yours," so a client
can't fingerprint ids outside their own access.

Scope resolution: `User.shipment_staff_id` (mirrors `team_leader_id`
exactly — see `app.models.auth.User`). A Shipment Staff user's scope is
"every Telecaller whose `shipment_staff_id` points at me." No new
Order-level column: `Order.confirmed_by_telecaller_id` (already the
single source of truth for "who confirmed this order") is reused
unchanged, joined against that Telecaller set.

Every operational method (create shipment / assign AWB / request pickup
/ refresh tracking / retry Shopify sync) does its own scope check first,
then delegates entirely to the SAME shared services Fulfillment/Admin
already use (`ShiprocketOperationsService`, `ShopifyFulfillmentService`)
— no duplicated business logic, no second Shiprocket/Shopify client.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, NotFoundError
from app.models.auth import User
from app.models.ndr import NDR
from app.models.order import Order
from app.models.rto import RTO
from app.models.shipment import Shipment, ShipmentEvent
from app.repositories.auth import UserRepository
from app.repositories.ndr import NDRRepository
from app.repositories.order import OrderRepository
from app.repositories.rto import RTORepository
from app.repositories.shipment import ShipmentRepository
from app.schemas.common import PageParams, SortParams
from app.services.shipment_service import ShipmentService
from app.services.shiprocket_service import ShiprocketOperationsService
from app.services.shopify_fulfillment_service import ShopifyFulfillmentService


class ShipmentStaffService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.orders = OrderRepository(session)
        self.shipments = ShipmentRepository(session)
        self.ndr = NDRRepository(session)
        self.rto = RTORepository(session)
        self.shipment_service = ShipmentService(session)
        self.shiprocket_ops = ShiprocketOperationsService(session)
        self.shopify_fulfillment = ShopifyFulfillmentService(session)

    async def resolve_scope(self, actor: User) -> list[uuid.UUID]:
        """Every Telecaller id this Shipment Staff user is permitted to
        see — always derived from `actor.id` itself, deliberately with
        no `is_superuser`/ADMIN bypass (unlike `resolve_team_scope`):
        there's no legitimate reason for this specific, dedicated router
        to return anything but "my own scope" for anyone, admin included
        — an admin who wants org-wide visibility uses the existing
        `/shipments/*`/`/orders/*` routes instead, same convention
        `resolve_telecaller_scope` already established.
        """
        return await self.users.list_ids_by_shipment_staff(actor.id)

    # ------------------------------------------------------------------
    # Scoped lookups
    # ------------------------------------------------------------------

    async def get_scoped_order(self, order_id: uuid.UUID, *, actor: User) -> Order:
        scope = await self.resolve_scope(actor)
        order = await self.orders.get_by_id_with_items_and_customer(order_id)
        if order is None or order.confirmed_by_telecaller_id not in scope:
            raise AuthorizationError("Order not found or not in your scope.")
        return order

    async def get_scoped_shipment(self, shipment_id: uuid.UUID, *, actor: User) -> Shipment:
        shipment = await self.shipments.get_by_id(shipment_id)
        if shipment is None:
            raise NotFoundError("Shipment not found.")
        scope = await self.resolve_scope(actor)
        order = await self.orders.get_by_id(shipment.order_id)
        if order is None or order.confirmed_by_telecaller_id not in scope:
            raise AuthorizationError("Shipment not found or not in your scope.")
        return shipment

    # ------------------------------------------------------------------
    # Confirmed Orders / Shipment Queue (scoped)
    # ------------------------------------------------------------------

    async def list_confirmed_orders(
        self,
        *,
        actor: User,
        page_params: PageParams,
        q: str | None = None,
        payment_type: str | None = None,
        courier_id: uuid.UUID | None = None,
        sku: str | None = None,
        shipment_status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[tuple[Order, Shipment | None]], int]:
        scope = await self.resolve_scope(actor)
        return await self.shipment_service.list_shipment_queue(
            page_params=page_params,
            q=q,
            payment_type=payment_type,
            courier_id=courier_id,
            sku=sku,
            shipment_status=shipment_status,
            date_from=date_from,
            date_to=date_to,
            scope_telecaller_ids=scope,
        )

    async def list_shipments(
        self,
        *,
        actor: User,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        status: str | None = None,
        courier_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[Shipment], int]:
        scope = await self.resolve_scope(actor)
        return await self.shipment_service.list_shipments(
            page_params=page_params,
            sort_params=sort_params,
            q=q,
            status=status,
            courier_id=courier_id,
            date_from=date_from,
            date_to=date_to,
            telecaller_ids=scope,
        )

    async def get_timeline(self, shipment_id: uuid.UUID, *, actor: User) -> list[ShipmentEvent]:
        await self.get_scoped_shipment(shipment_id, actor=actor)
        return await self.shipment_service.get_timeline(shipment_id)

    async def list_ndr(
        self,
        *,
        actor: User,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[NDR], int]:
        scope = await self.resolve_scope(actor)
        query = self.ndr.search_query(
            q=q, status=status, date_from=date_from, date_to=date_to, telecaller_ids=scope
        )
        items, total = await self.ndr.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def list_rto(
        self,
        *,
        actor: User,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[RTO], int]:
        scope = await self.resolve_scope(actor)
        query = self.rto.search_query(
            q=q, status=status, date_from=date_from, date_to=date_to, telecaller_ids=scope
        )
        items, total = await self.rto.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    # ------------------------------------------------------------------
    # Dashboard (scoped)
    # ------------------------------------------------------------------

    async def get_summary(self, *, actor: User) -> dict[str, object]:
        scope = await self.resolve_scope(actor)
        return await self.shipment_service.get_summary(scope_telecaller_ids=scope)

    async def get_analytics(
        self, *, actor: User, date_from: datetime | None = None, date_to: datetime | None = None
    ) -> dict[str, object]:
        scope = await self.resolve_scope(actor)
        return await self.shipment_service.get_analytics(
            date_from=date_from, date_to=date_to, scope_telecaller_ids=scope
        )

    # ------------------------------------------------------------------
    # Shipment processing (scoped) -- each delegates entirely to the
    # existing shared services after its own scope check; no duplicated
    # Shiprocket/Shopify logic.
    # ------------------------------------------------------------------

    async def create_shipment(
        self,
        order_id: uuid.UUID,
        *,
        actor: User,
        length_cm: float = 10.0,
        breadth_cm: float = 10.0,
        height_cm: float = 10.0,
        weight_kg: float = 0.5,
    ) -> Shipment:
        await self.get_scoped_order(order_id, actor=actor)
        return await self.shiprocket_ops.create_shipment_for_order(
            order_id,
            actor=actor,
            length_cm=length_cm,
            breadth_cm=breadth_cm,
            height_cm=height_cm,
            weight_kg=weight_kg,
        )

    async def assign_awb(
        self, shipment_id: uuid.UUID, *, actor: User, courier_id: str | None
    ) -> Shipment:
        await self.get_scoped_shipment(shipment_id, actor=actor)
        return await self.shiprocket_ops.assign_awb(shipment_id, actor=actor, courier_id=courier_id)

    async def bulk_process_existing_shipments_for_my_scope(
        self, order_ids: list[uuid.UUID], *, actor: User
    ) -> list[dict[str, object]]:
        """Scoped equivalent of `ShiprocketOperationsService.
        bulk_process_existing_shipments` -- every order is re-resolved
        through `get_scoped_order` first (never trusted from the client),
        so this can only ever process one of this Shipment Staff user's
        own scoped orders. Delegates the actual work to `ShiprocketOperations
        Service.process_shipment_for_order` (same as Fulfillment/Admin's
        `POST /orders/bulk-process-shipments`) -- no duplicated Shiprocket
        logic, and it NEVER creates a Shiprocket order for the identical
        reason that endpoint doesn't.
        """
        results: list[dict[str, object]] = []
        for order_id in order_ids:
            try:
                order = await self.get_scoped_order(order_id, actor=actor)
            except (NotFoundError, AuthorizationError):
                results.append(
                    {
                        "order_id": order_id,
                        "order_number": None,
                        "status": "failed",
                        "shiprocket_shipment_id": None,
                        "shiprocket_order_id": None,
                        "awb": None,
                        "courier_name": None,
                        "reason": "Order not found or not in your scope.",
                    }
                )
                continue
            results.append(
                await self.shiprocket_ops.process_shipment_for_order(order, actor=actor)
            )
        return results

    async def request_pickup(self, shipment_id: uuid.UUID, *, actor: User) -> Shipment:
        await self.get_scoped_shipment(shipment_id, actor=actor)
        return await self.shiprocket_ops.request_pickup(shipment_id, actor=actor)

    async def refresh_tracking(self, shipment_id: uuid.UUID, *, actor: User) -> Shipment:
        await self.get_scoped_shipment(shipment_id, actor=actor)
        return await self.shiprocket_ops.refresh_tracking_for_shipment(shipment_id, actor=actor)

    async def retry_shopify_sync(self, shipment_id: uuid.UUID, *, actor: User) -> Shipment:
        await self.get_scoped_shipment(shipment_id, actor=actor)
        return await self.shopify_fulfillment.sync_fulfillment_for_shipment(
            shipment_id, actor=actor
        )

    async def reattempt_ndr(
        self,
        ndr_id: uuid.UUID,
        *,
        actor: User,
        address_1: str,
        address_2: str | None,
        phone: str,
    ) -> NDR:
        ndr = await self.ndr.get_by_id(ndr_id)
        if ndr is None:
            raise NotFoundError("NDR not found.")
        scope = await self.resolve_scope(actor)
        order = await self.orders.get_by_id(ndr.order_id)
        if order is None or order.confirmed_by_telecaller_id not in scope:
            raise AuthorizationError("NDR not found or not in your scope.")
        return await self.shiprocket_ops.ndr_reattempt(
            ndr_id, actor=actor, address_1=address_1, address_2=address_2, phone=phone
        )

    # ------------------------------------------------------------------
    # Admin oversight (Part 7) -- NOT scoped; called only from an
    # ADMIN-gated endpoint. Every Shipment Staff user + their assigned
    # Telecaller count + aggregated confirm/ship/deliver/NDR/RTO figures.
    # ------------------------------------------------------------------

    async def list_shipment_staff_performance(self) -> list[dict[str, object]]:
        staff_users = await self.users.list_by_role("SHIPMENT_STAFF")
        results: list[dict[str, object]] = []
        for staff in staff_users:
            telecaller_ids = await self.users.list_ids_by_shipment_staff(staff.id)
            aggregate = {"confirmed": 0, "shipped": 0, "delivered": 0, "ndr": 0, "rto": 0}
            if telecaller_ids:
                counts = await self.orders.telecaller_confirmation_counts(
                    telecaller_ids=telecaller_ids
                )
                for per_telecaller in counts.values():
                    for key in aggregate:
                        aggregate[key] += per_telecaller[key]
            results.append(
                {
                    "shipment_staff_id": staff.id,
                    "shipment_staff_name": staff.name,
                    "telecaller_count": len(telecaller_ids),
                    **aggregate,
                }
            )
        results.sort(key=lambda r: r["confirmed"], reverse=True)  # type: ignore[arg-type,return-value]
        return results
