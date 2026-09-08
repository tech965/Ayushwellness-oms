"""OMS -> Shopify outbound fulfillment push (Phase 6).

Trigger point (deliberate design decision — see report): AWB assignment
(`ShiprocketOperationsService.assign_awb`), never Telecaller confirmation
and never bare shipment creation. AWB assignment is the first point in
the existing shipment lifecycle where there's a real courier + tracking
number worth showing the customer on their Shopify order, and it's the
first point that represents a real, committed shipping action rather
than just "a Shiprocket order object exists."

Failure isolation is the core contract here: a Shopify failure must
NEVER undo or fail the Shiprocket/OMS operation that triggered it. Every
public method here catches its own failures, records them on the
`Shipment` row (`shopify_sync_status`/`shopify_sync_error`), commits, and
returns normally — it never raises out to a caller that also has its own
(already-successful) Shiprocket state to preserve. The one exception is
`NotFoundError` for a genuinely bad `shipment_id`, which is a caller bug,
not an external-system failure.

Idempotency: a `Shipment` with `shopify_fulfillment_id` already set is
never re-pushed — reused unchanged on every subsequent call (including
manual retry). See `sync_fulfillment_for_shipment`'s docstring for how a
FAILED retry that turns out to have actually succeeded upstream is
detected without creating a second Shopify Fulfillment.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IntegrationError, NotFoundError
from app.core.logging import get_logger
from app.integrations.registry import get_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.auth import User
from app.models.enums import ShopifySyncStatus
from app.models.integration import IntegrationCode
from app.models.shipment import Shipment
from app.repositories.courier import CourierRepository
from app.repositories.order import OrderRepository
from app.repositories.shipment import ShipmentRepository
from app.services.audit_service import AuditService

logger = get_logger(__name__)

# Persisted error messages must never grow unboundedly (a pathological
# GraphQL error message, or one accidentally including a raw payload,
# could otherwise bloat the row) -- generous enough to stay readable in
# the UI it backs (Part 13).
_MAX_ERROR_MESSAGE_LENGTH = 2000


class ShopifyFulfillmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shipments = ShipmentRepository(session)
        self.orders = OrderRepository(session)
        self.couriers = CourierRepository(session)
        self.audit = AuditService(session)

    def _get_adapter(self) -> ShopifyAdapter:
        adapter = get_adapter(IntegrationCode.SHOPIFY)
        if adapter is None:
            raise IntegrationError(
                "No adapter registered for integration 'shopify'.",
                details={"error_type": "integration_error"},
            )
        return adapter  # type: ignore[return-value]

    async def sync_fulfillment_for_shipment(
        self, shipment_id: uuid.UUID, *, actor: User | None
    ) -> Shipment:
        """Pushes this shipment to Shopify as a real `Fulfillment`, or
        determines it doesn't need to be (not a Shopify order, no AWB
        yet). Safe to call more than once for the same shipment:

        - Already synced (`shopify_fulfillment_id` set) -> returned
          unchanged, Shopify is never called again.
        - Order isn't from Shopify (`Order.shopify_order_id` is `None`,
          i.e. a manually-created OMS order) -> `NOT_APPLICABLE`, no call.
        - No AWB yet -> left as `PENDING` (nothing meaningful to give
          Shopify as tracking info yet); the caller (`assign_awb`) only
          invokes this once an AWB exists, so this branch is really only
          reachable if the retry endpoint is called too early.
        - Shopify reports no remaining OPEN fulfillment order for this
          order: if this is the FIRST attempt, that's a legitimate
          "nothing to push" (`NOT_APPLICABLE`) -- e.g. an order Shopify
          already fulfilled through some other channel. If it's a RETRY
          after a previous `FAILED` attempt, it instead means the earlier
          attempt likely succeeded upstream even though the OMS lost
          track of the result (timeout, etc.) -- marked `SYNCED` (no
          `shopify_fulfillment_id`, since we genuinely don't know it, but
          never re-attempted either) rather than left permanently stuck
          retrying and risking a duplicate `fulfillmentCreate`.
        """
        shipment = await self.shipments.get_by_id(shipment_id)
        if shipment is None:
            raise NotFoundError("Shipment not found.")

        order = await self.orders.get_by_id(shipment.order_id)
        if order is None or not order.shopify_order_id:
            if shipment.shopify_sync_status != ShopifySyncStatus.NOT_APPLICABLE:
                await self.shipments.update(
                    shipment, shopify_sync_status=ShopifySyncStatus.NOT_APPLICABLE
                )
                await self.session.commit()
            return shipment

        if shipment.shopify_fulfillment_id:
            return shipment

        if not shipment.awb:
            return shipment

        previous_status = shipment.shopify_sync_status
        adapter = self._get_adapter()
        order_gid = f"gid://shopify/Order/{order.shopify_order_id}"

        try:
            fulfillment_order_id = await adapter.get_open_fulfillment_order_id(order_gid)
            if fulfillment_order_id is None:
                if previous_status == ShopifySyncStatus.FAILED:
                    await self.shipments.update(
                        shipment,
                        shopify_sync_status=ShopifySyncStatus.SYNCED,
                        shopify_sync_error=(
                            "Shopify reports no remaining open fulfillment orders for this "
                            "order -- treating as already fulfilled (likely by an earlier "
                            "attempt whose result the OMS failed to record)."
                        ),
                        shopify_synced_at=datetime.now(UTC),
                    )
                else:
                    await self.shipments.update(
                        shipment, shopify_sync_status=ShopifySyncStatus.NOT_APPLICABLE
                    )
                await self.session.commit()
                return shipment

            courier = (
                await self.couriers.get_by_id(shipment.courier_id) if shipment.courier_id else None
            )
            fulfillment = await adapter.create_fulfillment(
                fulfillment_order_id=fulfillment_order_id,
                tracking_number=shipment.awb,
                tracking_company=courier.name if courier else None,
                tracking_url=None,
                notify_customer=False,
            )
        except IntegrationError as exc:
            return await self._record_failure(shipment, actor=actor, message=exc.message)
        except Exception as exc:  # noqa: BLE001 - never let an unexpected error corrupt state
            await self.session.rollback()
            shipment = await self.shipments.get_by_id(shipment_id)
            assert shipment is not None
            return await self._record_failure(
                shipment, actor=actor, message=f"Unexpected error syncing to Shopify: {exc}"
            )

        await self.shipments.update(
            shipment,
            shopify_fulfillment_id=fulfillment["id"],
            shopify_sync_status=ShopifySyncStatus.SYNCED,
            shopify_sync_error=None,
            shopify_synced_at=datetime.now(UTC),
        )
        await self.audit.record(
            user=actor,
            action="shipment.shopify_fulfillment_synced",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={"shopify_fulfillment_id": fulfillment["id"]},
        )
        await self.session.commit()
        logger.info(
            "shopify_fulfillment_synced",
            shipment_id=str(shipment.id),
            order_id=str(order.id),
        )
        return shipment

    async def _record_failure(
        self, shipment: Shipment, *, actor: User | None, message: str
    ) -> Shipment:
        truncated = message[:_MAX_ERROR_MESSAGE_LENGTH]
        await self.shipments.update(
            shipment,
            shopify_sync_status=ShopifySyncStatus.FAILED,
            shopify_sync_error=truncated,
        )
        await self.audit.record(
            user=actor,
            action="shipment.shopify_fulfillment_sync_failed",
            entity_type="shipment",
            entity_id=str(shipment.id),
            new_value={"error": truncated},
        )
        await self.session.commit()
        logger.warning(
            "shopify_fulfillment_sync_failed", shipment_id=str(shipment.id), error=truncated
        )
        return shipment
