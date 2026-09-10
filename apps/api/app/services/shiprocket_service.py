"""OMS -> Shiprocket operational actions (spec §7/§26): create a
Shiprocket shipment from an OMS order, assign an AWB, cancel, request
pickup, refresh tracking on demand, and act on an NDR. Each action calls
the Shiprocket adapter directly (this service is explicitly
Shiprocket-aware, the same way `app/api/v1/webhooks/shopify.py` is
explicitly Shopify-aware) but every OMS write goes through the existing
services/repositories — never a raw session mutation — and OMS state is
only updated *after* Shiprocket confirms success (spec §17).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, IntegrationError, NotFoundError
from app.core.logging import get_logger
from app.integrations.entity_sync import ENTITY_UPSERT_HANDLERS
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.config import ShiprocketConfig
from app.integrations.shiprocket.normalizer import TRACKING_NORMALIZER, extract_tracking_events
from app.models.auth import User
from app.models.enums import (
    FulfillmentStatus,
    NDRStatus,
    OrderStatus,
    ShipmentStatus,
    ShopifySyncStatus,
)
from app.models.integration import IntegrationCode
from app.models.ndr import NDR
from app.models.order import Order
from app.models.shipment import Shipment
from app.repositories.order import OrderRepository
from app.repositories.shipment import ShipmentRepository
from app.services.audit_service import AuditService
from app.services.courier_service import CourierService
from app.services.inventory_service import InventoryService
from app.services.ndr_service import NDRService
from app.services.shipment_service import ShipmentService
from app.services.shopify_fulfillment_service import ShopifyFulfillmentService

logger = get_logger(__name__)

# The Shiprocket seller-dashboard "Ready to Ship" page -- ALWAYS opened
# plain, with no query string. Shiprocket support has confirmed
# `order_ids` (or any other deep-link filter param) is NOT an officially
# supported way to pre-filter this page -- an earlier version of this
# feature tried exactly that (`?order_ids={id}`) and it silently did
# nothing (Shiprocket's own page resets/ignores unrecognized query
# state). The supported alternative: open this plain page and give the
# operator the real numeric Shiprocket order id to paste into
# Shiprocket's own "Multiple Order IDs" filter -- see
# `shiprocket_order_id()` below, and the frontend's `openShiprocketOrder`
# handlers (`ShipmentActionCell` et al.), which open this URL and copy
# the id to the clipboard together.
SHIPROCKET_READY_TO_SHIP_URL = "https://app.shiprocket.in/seller/orders/readytoship"


def shiprocket_order_id(shipment: Shipment | None) -> str | None:
    """The real, numeric Shiprocket order id for `shipment`, or `None`
    when the OMS has no reliably-stored one for it -- never fabricated
    from the OMS order number or any other guess (see the Fulfillment
    Queue's "Process Shipment"/"Ship Order" actions, which open
    Shiprocket's plain "Ready to Ship" page and copy this id to the
    clipboard, rather than calling `create_shipment_for_order` again --
    creating a second Shiprocket order for one this account may already
    have, e.g. via Shiprocket's own Shopify channel connector, is exactly
    the duplicate-shipment risk this exists to avoid).

    The id itself: `create_shipment_for_order` persists the FULL
    `/orders/create/adhoc` response verbatim on `Shipment.raw_
    external_payload` (see that method) -- that response's `order_id`
    key is Shiprocket's own numeric order id, distinct from `shipment_
    id` (which `shiprocket_shipment_id`/`external_id` already store).
    No other code path in this codebase currently persists that id
    anywhere else on `Shipment` (confirmed against `entity_sync.
    _upsert_shipment`, which only ever uses a pulled shipment's own
    `shiprocket_order_id` transiently, to resolve which OMS `Order` it
    belongs to, then discards it) -- a `Shipment` pulled in from
    Shiprocket rather than created via this OMS's own push (i.e. every
    Shiprocket order picked up by Shiprocket's Shopify channel connector
    before this OMS ever pushed it) has no `raw_external_payload["order_
    id"]` to read, and this correctly returns `None` for it rather than
    guessing.
    """
    if shipment is None:
        return None
    payload = shipment.raw_external_payload
    if not payload:
        return None
    order_id = payload.get("order_id")
    if not order_id:
        return None
    # Defense in depth: `order_id` above already rejects None/0/""/[]/{},
    # but not a value that's truthy yet renders as nothing usable (e.g. a
    # whitespace-only string from a malformed sync record) -- stringified
    # and stripped before being handed to the frontend, so a degenerate
    # value can never be copied/displayed as a blank id. Real Shiprocket
    # order ids are always plain digits in every confirmed-live sample
    # this engagement has seen; a non-digit value is logged (never raised
    # -- reading this must never crash a page render) so a genuinely bad
    # stored value is visible in production logs instead of silently
    # being offered to an operator as if it were usable.
    order_id_str = str(order_id).strip()
    if not order_id_str or not order_id_str.isdigit():
        logger.warning(
            "shiprocket_order_id_not_usable",
            shipment_id=str(shipment.id),
            order_id_repr=repr(order_id),
        )
        return None
    return order_id_str


# Bounds on the live, on-demand scan `locate_shiprocket_orders` runs when
# an order has no local `Shipment` row yet (e.g. it reached Shiprocket
# only via Shiprocket's own Shopify channel connector, and the periodic
# `shipments` sync -- see `app.tasks.sync_tasks._SCHEDULED_SYNC_ENTITIES`
# -- hasn't caught up to it yet). This is a synchronous, request-bound
# operation, not a background crawl, so both the number of `/shipments`
# pages fetched and the number of candidate records actually resolved
# (each of which can cost one live `GET /orders/show/{id}` call -- see
# `entity_sync._upsert_shipment`) must stay small enough to finish inside
# one HTTP request. A locate attempt that exhausts these bounds without a
# match reports "not found" rather than guessing -- the periodic sync
# remains the backstop that will eventually resolve it regardless.
_LOCATE_MAX_PAGES = 2
_LOCATE_MAX_CANDIDATES = 30


async def locate_shiprocket_orders(
    session: AsyncSession, orders: list[Order]
) -> dict[uuid.UUID, str | None]:
    """Resolves each of `orders` to its existing Shiprocket order id --
    NEVER creates a Shiprocket order (no `orders/create/adhoc` call
    anywhere in this function or anything it calls).

    Two steps, in order:

    1. A local-only check: any `orders` that already have a `Shipment`
       row with a usable `raw_external_payload["order_id"]` (created
       either by this OMS's own `create_shipment_for_order` push, or by
       a previous run of the periodic Shiprocket `shipments` sync /
       a previous call to this same function) resolve immediately, with
       no Shiprocket API call at all.

    2. For everything still unresolved: a bounded, newest-first live scan
       of Shiprocket's `/shipments` list (the one confirmed-live list
       endpoint -- see `ShiprocketAdapter._FETCH_ROUTES`), reusing
       `entity_sync.ENTITY_UPSERT_HANDLERS["shipments"]` -- the EXACT
       same matching logic (exact `channel_order_id`/`api_order_id`
       identity only, see that module's docstring; never a fuzzy match
       on name/phone/amount) and persistence path the periodic sync
       itself uses -- run here on-demand instead of waiting for its next
       scheduled cycle. A genuine match is persisted as a real `Shipment`
       row (via `ShipmentService.upsert_synced_shipment`, the same as
       every other sync path), so the id is "exposed/stored... for future
       use" exactly once, not re-derived on every later click.

    Bounded by `_LOCATE_MAX_PAGES`/`_LOCATE_MAX_CANDIDATES` (see their
    docstring) -- an order whose match isn't found within that budget
    resolves to `None` here (never a guess), and stays resolvable by the
    next periodic sync cycle regardless.
    """
    results: dict[uuid.UUID, str | None] = {}
    pending: dict[uuid.UUID, Order] = {}
    for order in orders:
        existing_shipments = await ShipmentRepository(session).list_for_order(order.id)
        order_id_found = next(
            (i for s in existing_shipments if (i := shiprocket_order_id(s)) is not None), None
        )
        if order_id_found:
            results[order.id] = order_id_found
        else:
            pending[order.id] = order

    if not pending:
        return results

    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    if not isinstance(adapter, ShiprocketAdapter):
        for order_id in pending:
            results[order_id] = None
        return results

    since = min(order.order_datetime for order in pending.values())
    cursor: str | None = None
    candidates_examined = 0
    handler = ENTITY_UPSERT_HANDLERS["shipments"]

    try:
        for _page_num in range(_LOCATE_MAX_PAGES):
            page = await adapter.fetch_incremental(
                "shipments", since=since, cursor=cursor, limit=50
            )
            for raw in page.nodes:
                if not pending or candidates_examined >= _LOCATE_MAX_CANDIDATES:
                    break
                candidates_examined += 1
                try:
                    normalized = adapter.normalize("shipments", raw)
                    upserted, _created = await handler(session, normalized)
                except Exception:  # noqa: BLE001 - one bad candidate must not abort the scan
                    await session.rollback()
                    continue
                await session.commit()
                if upserted.order_id in pending:
                    results[upserted.order_id] = shiprocket_order_id(upserted)
                    del pending[upserted.order_id]
            if not pending or not page.has_more or candidates_examined >= _LOCATE_MAX_CANDIDATES:
                break
            cursor = page.next_cursor
    except IntegrationError:
        # Couldn't even fetch a page (not configured, auth, network) --
        # everything still `pending` falls through to "not found" below,
        # same as a bounded scan that genuinely found nothing.
        await session.rollback()

    for order_id in pending:
        results[order_id] = None
    return results


class ShiprocketOperationsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.shipments = ShipmentRepository(session)
        self.shipment_service = ShipmentService(session)
        self.courier_service = CourierService(session)
        self.ndr_service = NDRService(session)
        self.inventory_service = InventoryService(session)
        self.shopify_fulfillment_service = ShopifyFulfillmentService(session)
        self.audit = AuditService(session)

    def _get_adapter(self) -> ShiprocketAdapter:
        adapter = get_adapter(IntegrationCode.SHIPROCKET)
        if adapter is None:
            raise IntegrationError(
                "No adapter registered for integration 'shiprocket'.",
                details={"error_type": "integration_error"},
            )
        return adapter  # type: ignore[return-value]

    async def _get_shipment(self, shipment_id: uuid.UUID) -> Shipment:
        shipment = await self.shipments.get_by_id(shipment_id)
        if shipment is None:
            raise NotFoundError("Shipment not found.")
        return shipment

    async def _check_shippable(self, order: Order) -> None:
        """The single source of truth for "can a Shiprocket shipment be
        created for this order right now" — raises `ConflictError` with a
        human-readable reason on the first check that fails, otherwise
        returns normally. Shared by `create_shipment_for_order` (which
        actually executes) and `validate_shipment_eligibility` (a
        read-only dry run for the bulk-ship confirmation screen) so the
        two can never drift apart on what "eligible" means — never a
        second, looser copy of these rules.

        Deliberately does NOT check Shiprocket configuration (an
        environment-wide concern, not a per-order one — checked once by
        `create_shipment_for_order` itself, not repeated per order in a
        bulk validation pass).
        """
        if order.status != OrderStatus.CONFIRMED:
            raise ConflictError(
                f"Order is '{order.status.value}', not confirmed — cannot ship.",
                details={"error_type": "not_confirmed"},
            )
        if order.fulfillment_status == FulfillmentStatus.FULFILLED:
            raise ConflictError(
                "Order is already fulfilled.",
                details={"error_type": "already_fulfilled"},
            )
        if not order.shipping_address:
            raise ConflictError(
                "Order has no shipping address on file.",
                details={"error_type": "missing_shipping_address"},
            )

        # Idempotency guard (spec: "prevent duplicate external operations" —
        # a client retry after a timeout, or a double-click, must never
        # create a second Shiprocket order for the same OMS order). A
        # CANCELLED shipment doesn't block a genuine re-ship. This is a
        # local-only check (no new Shiprocket API call) — cheap and safe
        # to run before ever touching the adapter.
        existing_shipments = await self.shipments.list_for_order(order.id)
        if any(s.current_status != ShipmentStatus.CANCELLED for s in existing_shipments):
            raise ConflictError(
                "A shipment already exists for this order — refresh the page instead of "
                "creating another one.",
                details={"error_type": "shipment_already_exists"},
            )

        shortages = await self.inventory_service.check_stock_available(order.id)
        if shortages:
            raise ConflictError(
                "Cannot create shipment — insufficient stock for one or more items.",
                details={"error_type": "insufficient_stock", "items": shortages},
            )

    async def validate_shipment_eligibility(
        self, order_ids: list[uuid.UUID]
    ) -> list[dict[str, object]]:
        """Read-only dry run of `_check_shippable` for every selected
        order — never creates or touches anything, purely classifies each
        order as ready/not-ready with a reason, for the bulk-ship
        confirmation screen ("3 ready, 2 cannot be shipped"). Reuses the
        exact same eligibility rules `create_shipment_for_order` itself
        enforces; never a second, looser copy of them.
        """
        results: list[dict[str, object]] = []
        for order_id in order_ids:
            order = await self.orders.get_by_id_with_items_and_customer(order_id)
            if order is None:
                results.append({"order_id": order_id, "ready": False, "reason": "Order not found."})
                continue
            try:
                await self._check_shippable(order)
            except ConflictError as exc:
                results.append({"order_id": order_id, "ready": False, "reason": exc.message})
            else:
                results.append({"order_id": order_id, "ready": True, "reason": None})
        return results

    async def create_shipment_for_order(
        self,
        order_id: uuid.UUID,
        *,
        actor: User | None,
        length_cm: float = 10.0,
        breadth_cm: float = 10.0,
        height_cm: float = 10.0,
        weight_kg: float = 0.5,
    ) -> Shipment:
        order = await self.orders.get_by_id_with_items_and_customer(order_id)
        if order is None:
            raise NotFoundError("Order not found.")

        await self._check_shippable(order)

        config = ShiprocketConfig.from_settings()
        if config is None or not config.pickup_location:
            raise IntegrationError(
                "Shiprocket is not configured with a pickup location "
                "(SHIPROCKET_EMAIL/SHIPROCKET_PASSWORD/SHIPROCKET_PICKUP_LOCATION).",
                details={"error_type": "not_configured"},
            )

        adapter = self._get_adapter()
        response = await adapter.create_order(
            order,
            pickup_location=config.pickup_location,
            length_cm=length_cm,
            breadth_cm=breadth_cm,
            height_cm=height_cm,
            weight_kg=weight_kg,
        )

        shiprocket_shipment_id = response.get("shipment_id")
        if not shiprocket_shipment_id:
            raise IntegrationError(
                "Shiprocket order creation response did not include a shipment_id.",
                details={"error_type": "validation_error"},
            )

        shipment, _created = await self.shipment_service.upsert_synced_shipment(
            source_system="shiprocket",
            external_id=str(shiprocket_shipment_id),
            order_id=order.id,
            shiprocket_shipment_id=str(shiprocket_shipment_id),
            raw_external_payload=response,
        )

        # Marks this shipment as eligible for the outbound Shopify push
        # once it has real tracking info to send (see
        # `ShopifyFulfillmentService`'s module docstring for why AWB
        # assignment, not this step, is the actual trigger) — a plain
        # OMS-manual order (no `shopify_order_id`) is never eligible.
        # Creating a shipment never itself talks to Shopify.
        initial_shopify_status = (
            ShopifySyncStatus.PENDING
            if order.shopify_order_id
            else ShopifySyncStatus.NOT_APPLICABLE
        )
        await self.shipments.update(shipment, shopify_sync_status=initial_shopify_status)

        await self.audit.record(
            user=actor,
            action="shipment.created_via_shiprocket",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={
                "order_id": str(order.id),
                "shiprocket_order_id": response.get("order_id"),
                "shiprocket_shipment_id": shiprocket_shipment_id,
            },
        )
        await self.session.commit()
        return shipment

    async def bulk_create_shipments_for_orders(
        self, order_ids: list[uuid.UUID], *, actor: User | None
    ) -> list[dict[str, object]]:
        """Creates a Shiprocket shipment for each selected order
        independently — one order's failure (not found, insufficient
        stock, Shiprocket not configured) never blocks or rolls back the
        rest; per-order results are returned instead of raising. Same
        per-order-safe pattern as
        `TelecallingService.bulk_confirm_assigned_orders`. Used by the
        Fulfillment Queue's bulk "Ship via Shiprocket" action.
        """
        results: list[dict[str, object]] = []
        for order_id in order_ids:
            try:
                shipment = await self.create_shipment_for_order(order_id, actor=actor)
            except (NotFoundError, ConflictError, IntegrationError) as exc:
                results.append(
                    {
                        "order_id": order_id,
                        "success": False,
                        "message": exc.message,
                        "shipment_id": None,
                    }
                )
            except Exception:
                await self.session.rollback()
                results.append(
                    {
                        "order_id": order_id,
                        "success": False,
                        "message": "Could not create a shipment for this order.",
                        "shipment_id": None,
                    }
                )
            else:
                results.append(
                    {
                        "order_id": order_id,
                        "success": True,
                        "message": None,
                        "shipment_id": shipment.id,
                    }
                )
        return results

    async def assign_awb(
        self, shipment_id: uuid.UUID, *, actor: User | None, courier_id: str | None
    ) -> Shipment:
        shipment = await self._get_shipment(shipment_id)
        if not shipment.shiprocket_shipment_id:
            raise ConflictError(
                "Shipment has no Shiprocket shipment id — create it via Shiprocket first."
            )

        adapter = self._get_adapter()
        response = await adapter.assign_awb(shipment.shiprocket_shipment_id, courier_id=courier_id)
        data = response.get("response", {}).get("data", response)
        awb_code = data.get("awb_code")
        courier_name = data.get("courier_name")
        courier_company_id = data.get("courier_company_id") or courier_id

        update_fields: dict[str, object] = {}
        if awb_code:
            update_fields["awb"] = awb_code
        if courier_name and courier_company_id:
            courier, _ = await self.courier_service.upsert_synced_courier(
                source_system="shiprocket",
                external_id=str(courier_company_id),
                name=courier_name,
            )
            update_fields["courier_id"] = courier.id
        if update_fields:
            await self.shipments.update(shipment, **update_fields)

        await self.audit.record(
            user=actor,
            action="shipment.awb_assigned",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={"awb": awb_code, "courier": courier_name},
        )
        await self.session.commit()

        # Trigger point for the outbound Shopify push (see
        # `ShopifyFulfillmentService`'s module docstring) — only once the
        # AWB assignment above is already fully committed and successful.
        # Never raises: a Shopify failure is recorded on the shipment
        # (`shopify_sync_status=FAILED`) and returned normally, it must
        # never fail or roll back this already-successful AWB assignment.
        if awb_code:
            shipment = await self.shopify_fulfillment_service.sync_fulfillment_for_shipment(
                shipment.id, actor=actor
            )
        return shipment

    async def cancel_shipment(self, shipment_id: uuid.UUID, *, actor: User | None) -> Shipment:
        shipment = await self._get_shipment(shipment_id)
        if not shipment.shiprocket_shipment_id:
            raise ConflictError(
                "Shipment has no Shiprocket shipment id — nothing to cancel in Shiprocket."
            )

        adapter = self._get_adapter()
        await adapter.cancel_shipment([shipment.shiprocket_shipment_id])

        previous_status = shipment.current_status
        await self.shipments.update(shipment, current_status=ShipmentStatus.CANCELLED)

        await self.audit.record(
            user=actor,
            action="shipment.cancelled_via_shiprocket",
            entity_type="shipment",
            entity_id=str(shipment.id),
            previous_value={"status": previous_status.value},
            new_value={"status": ShipmentStatus.CANCELLED.value},
        )
        await self.session.commit()
        return shipment

    async def request_pickup(self, shipment_id: uuid.UUID, *, actor: User | None) -> Shipment:
        shipment = await self._get_shipment(shipment_id)
        if not shipment.shiprocket_shipment_id:
            raise ConflictError(
                "Shipment has no Shiprocket shipment id — create it via Shiprocket first."
            )

        adapter = self._get_adapter()
        response = await adapter.request_pickup(shipment.shiprocket_shipment_id)

        await self.shipment_service.add_tracking_event(
            shipment.id,
            external_event_id=None,
            status="PICKUP SCHEDULED",
            location=None,
            event_timestamp=datetime.now(UTC),
            description="Pickup requested via Shiprocket.",
            courier_name=None,
            source="shiprocket",
            raw_payload=response,
        )

        await self.audit.record(
            user=actor,
            action="shipment.pickup_requested",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={},
        )
        await self.session.commit()
        return await self._get_shipment(shipment_id)

    async def refresh_tracking_for_shipment(
        self, shipment_id: uuid.UUID, *, actor: User | None
    ) -> Shipment:
        shipment = await self._get_shipment(shipment_id)
        if not shipment.awb:
            raise ConflictError("Shipment has no AWB yet — assign one first.")

        adapter = self._get_adapter()
        raw_response = await adapter.get_tracking(shipment.awb)

        for raw_event in extract_tracking_events(raw_response):
            normalized = TRACKING_NORMALIZER.normalize_event(raw_event)
            if normalized["event_timestamp"] is None:
                continue
            await self.shipment_service.add_tracking_event(
                shipment.id,
                external_event_id=normalized["external_event_id"],
                status=normalized["status"],
                location=normalized["location"],
                event_timestamp=normalized["event_timestamp"],
                description=normalized["description"],
                courier_name=normalized["courier_name"],
                source="shiprocket",
                raw_payload=normalized["raw_payload"],
            )
            if normalized["mapped_status"] is not None:
                await self.shipment_service.update_shipment(
                    shipment.id, actor=actor, current_status=normalized["mapped_status"]
                )

        await self.audit.record(
            user=actor,
            action="shipment.tracking_refreshed",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={},
        )
        await self.session.commit()
        return await self._get_shipment(shipment_id)

    async def ndr_reattempt(
        self,
        ndr_id: uuid.UUID,
        *,
        actor: User | None,
        address_1: str,
        address_2: str | None,
        phone: str,
    ) -> NDR:
        ndr = await self.ndr_service.get_ndr(ndr_id)
        shipment = await self.shipments.get_by_id(ndr.shipment_id)
        if shipment is None or not shipment.awb:
            raise ConflictError("This NDR's shipment has no AWB — cannot request a reattempt.")

        adapter = self._get_adapter()
        await adapter.ndr_reattempt(
            awb=shipment.awb, address_1=address_1, address_2=address_2, phone=phone
        )

        updated = await self.ndr_service.update_ndr(
            ndr_id,
            actor=actor,
            status=NDRStatus.REATTEMPT_SCHEDULED,
            reattempt_status="requested",
            customer_response=f"Reattempt requested: {address_1}",
        )

        await self.audit.record(
            user=actor,
            action="ndr.reattempt_requested",
            entity_type="ndr",
            entity_id=str(ndr_id),
            new_value={"address_1": address_1, "phone": phone},
        )
        await self.session.commit()
        return updated
