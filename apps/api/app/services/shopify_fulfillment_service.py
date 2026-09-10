"""OMS -> Shopify outbound fulfillment push (Phase 6, extended for
Telecaller-confirmation sync).

Two independent pushes live here, each with its own id/status columns so
neither can ever be mistaken for or cancel the other:

  - Real shipping (`sync_fulfillment_for_shipment`): triggered by AWB
    assignment (`ShiprocketOperationsService.assign_awb`), never
    Telecaller confirmation and never bare shipment creation. Tracked on
    `Shipment.shopify_fulfillment_id`/`shopify_sync_status`.
  - Telecaller confirmation (`sync_confirmation_fulfillment` /
    `reverse_confirmation_fulfillment`): triggered by `OrderService.
    confirm_order`/`unconfirm_order`. Marks the Shopify order Fulfilled
    purely to reflect "OMS confirmed this order" — no AWB, no Shiprocket
    shipment. Tracked on `Order.shopify_confirmation_fulfillment_id`/
    `shopify_confirmation_sync_status`.

Because a Telecaller confirmation can close the order's only
FulfillmentOrder before any real shipment exists, `sync_fulfillment_for_
shipment` has a fallback: if there's no remaining OPEN FulfillmentOrder
*and* the order has a `shopify_confirmation_fulfillment_id`, it attaches
real tracking to that SAME Fulfillment (`fulfillmentTrackingInfoUpdate`)
instead of trying to create a second one. This is the one place the two
pushes touch — see that method's docstring.

Failure isolation is the core contract here: a Shopify failure must
NEVER undo or fail the Shiprocket/OMS operation that triggered it. Every
public method here catches its own failures, records them on the
relevant row (`shopify_sync_status`/`shopify_sync_error` or their
`shopify_confirmation_*` counterparts), commits, and returns normally —
it never raises out to a caller that also has its own (already-
successful) OMS state to preserve. The one exception is
`reverse_confirmation_fulfillment`, which DOES raise on a fulfillment-
cancellation failure: `unconfirm_order`'s existing safety contract
(mirrors its Shiprocket-cancel-first rule) requires the whole revert to
abort — order stays CONFIRMED — rather than silently leave Shopify
Fulfilled while OMS calls the order PENDING. `NotFoundError` for a
genuinely bad `shipment_id`/`order_id` is likewise a caller bug, not an
external-system failure, and always raises.

Idempotency: a row with its `shopify_fulfillment_id` (or
`shopify_confirmation_fulfillment_id`) already set is never re-pushed —
reused unchanged on every subsequent call (including manual retry). See
`sync_fulfillment_for_shipment`'s docstring for how a FAILED retry that
turns out to have actually succeeded upstream is detected without
creating a second Shopify Fulfillment; `sync_confirmation_fulfillment`
has the identical guard for the confirmation push.
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
from app.models.order import Order
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

# Pushed to the Shopify order on Telecaller confirmation (`OrderService.
# confirm_order`), alongside (never instead of) the real confirmation
# Fulfillment `sync_confirmation_fulfillment` creates -- a marker proving
# the OMS is driving the workflow. Removed again by `unconfirm_order` via
# `reverse_confirmation_fulfillment`. Purely informational either way:
# never read back as a business-state source (Order.status/confirmation
# columns stay authoritative -- see module docstring).
CONFIRMATION_TAG = "OMS Confirmed"


def _to_shopify_address_input(address: dict) -> dict[str, str | None]:
    """Maps the OMS's own `Order.shipping_address` shape (`line1`/
    `line2`/`city`/`state`/`country`/`pin_code`/`contact_name`/
    `contact_phone` -- see `app.integrations.shopify.normalizer.
    normalize_address`, the same shape this was originally populated
    from) onto Shopify's `MailingAddressInput` field names. Shopify has
    no single "full name" input field, only `firstName`/`lastName`, so
    `contact_name` is split on its first space -- a reasonable, harmless
    best-effort mapping for how Shopify itself displays these two fields
    concatenated back into one name; never blocks the update if there's
    no space (the whole name goes to `lastName`, `firstName` left unset).
    """
    contact_name = (address.get("contact_name") or "").strip()
    if " " in contact_name:
        first_name, last_name = contact_name.split(" ", 1)
    else:
        first_name, last_name = None, (contact_name or None)
    return {
        "firstName": first_name or None,
        "lastName": last_name or None,
        "address1": address.get("line1"),
        "address2": address.get("line2"),
        "city": address.get("city"),
        "province": address.get("state"),
        "country": address.get("country"),
        "zip": address.get("pin_code"),
        "phone": address.get("contact_phone"),
    }


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
          order AND `Order.shopify_confirmation_fulfillment_id` is set:
          Telecaller confirmation already closed the order's one
          FulfillmentOrder by creating a Fulfillment of its own (see
          `sync_confirmation_fulfillment`) -- there is genuinely nothing
          left to `fulfillmentCreate`. Real tracking is attached to that
          SAME Fulfillment instead (`fulfillmentTrackingInfoUpdate`),
          which both marks this shipment `SYNCED` with that fulfillment's
          id AND is what makes the Shopify order actually show the AWB/
          courier once real shipping happens, rather than silently
          no-oping just because confirmation got there first.
        - Otherwise, no remaining OPEN fulfillment order: if this is the
          FIRST attempt, that's a legitimate "nothing to push"
          (`NOT_APPLICABLE`) -- e.g. an order Shopify already fulfilled
          through some other channel. If it's a RETRY after a previous
          `FAILED` attempt, it instead means the earlier attempt likely
          succeeded upstream even though the OMS lost track of the result
          (timeout, etc.) -- marked `SYNCED` (no `shopify_fulfillment_id`,
          since we genuinely don't know it, but never re-attempted
          either) rather than left permanently stuck retrying and risking
          a duplicate `fulfillmentCreate`.
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
            courier = (
                await self.couriers.get_by_id(shipment.courier_id) if shipment.courier_id else None
            )
            if fulfillment_order_id is None:
                if order.shopify_confirmation_fulfillment_id:
                    fulfillment = await adapter.update_fulfillment_tracking(
                        fulfillment_id=order.shopify_confirmation_fulfillment_id,
                        tracking_number=shipment.awb,
                        tracking_company=courier.name if courier else None,
                        tracking_url=None,
                        notify_customer=False,
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
                        action="shipment.shopify_tracking_synced_to_confirmation_fulfillment",
                        entity_type="shipment",
                        entity_id=str(shipment.id),
                        new_value={"shopify_fulfillment_id": fulfillment["id"]},
                    )
                    await self.session.commit()
                    logger.info(
                        "shopify_tracking_synced_to_confirmation_fulfillment",
                        shipment_id=str(shipment.id),
                        order_id=str(order.id),
                    )
                    return shipment
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

    async def sync_confirmation_tag(self, order_id: uuid.UUID, *, actor: User | None) -> None:
        """Best-effort outbound push of `CONFIRMATION_TAG` to the Shopify
        order once a Telecaller confirms it in the OMS -- a purely
        informational marker proving the OMS is driving the workflow.
        Independent of the actual Fulfillment push (`sync_confirmation_
        fulfillment`, called separately by `OrderService.confirm_order`)
        -- a failure here never blocks that, and vice versa, so either
        half can be retried without redoing the other.

        Skipped entirely for a non-Shopify order (`shopify_order_id` is
        `None`, e.g. a manually-created OMS order) -- nothing to tag.
        Idempotent on Shopify's side (`tagsAdd` is a set-union; re-adding
        an existing tag is a no-op), so a re-confirm/retry never creates a
        duplicate tag and needs no local "already tagged" bookkeeping of
        its own.

        Never raises: same failure-isolation contract as
        `sync_fulfillment_for_shipment` -- confirming an order in the OMS
        must never be blocked, delayed, or rolled back by a Shopify-side
        failure. A failure here is logged only; there is no per-shipment
        "Retry" affordance for this the way there is for fulfillment sync,
        since the next successful confirm/resync naturally retries it.
        """
        order = await self.orders.get_by_id(order_id)
        if order is None or not order.shopify_order_id:
            return

        adapter = self._get_adapter()
        order_gid = f"gid://shopify/Order/{order.shopify_order_id}"
        try:
            await adapter.add_order_tags(order_gid, [CONFIRMATION_TAG])
        except IntegrationError as exc:
            logger.warning(
                "shopify_confirmation_tag_sync_failed", order_id=str(order_id), error=exc.message
            )
            return
        except Exception as exc:  # noqa: BLE001 - never let an unexpected error block confirmation
            logger.warning(
                "shopify_confirmation_tag_sync_failed", order_id=str(order_id), error=str(exc)
            )
            return

        await self.audit.record(
            user=actor,
            action="order.shopify_confirmation_tag_synced",
            entity_type="order",
            entity_id=str(order_id),
            new_value={"tag": CONFIRMATION_TAG},
        )
        await self.session.commit()
        logger.info("shopify_confirmation_tag_synced", order_id=str(order_id))

    async def sync_confirmation_fulfillment(
        self, order_id: uuid.UUID, *, actor: User | None
    ) -> Order:
        """Pushes the Telecaller confirmation to Shopify as a real
        `Fulfillment` -- no AWB, no tracking, no Shiprocket shipment
        involved; purely "OMS confirmed this order." Safe to call more
        than once for the same order (including from the manual retry
        endpoint and from `OrderService.confirm_order` on a bulk-confirm
        retry hitting an already-confirmed order):

        - Already synced (`shopify_confirmation_fulfillment_id` set) ->
          returned unchanged, Shopify is never called again.
        - Order isn't from Shopify (`shopify_order_id` is `None`, i.e. a
          manually-created OMS order) -> `NOT_APPLICABLE`, no call.
        - Shopify reports no remaining OPEN fulfillment order: if this is
          the FIRST attempt, that's a legitimate "nothing to push"
          (`NOT_APPLICABLE`) -- e.g. an order Shopify already fulfilled
          through some other channel. If it's a RETRY after a previous
          `FAILED` attempt, the earlier attempt likely succeeded upstream
          even though the OMS lost track of the result -- marked `SYNCED`
          (no `shopify_confirmation_fulfillment_id`, since we genuinely
          don't know it, but never re-attempted either). That one
          combination -- `SYNCED` with a `None` id -- is exactly what
          `unconfirm_order` treats as unreversible and blocks on, same as
          an order fulfilled directly through Shopify with no OMS record
          of how.

        Never raises: same failure-isolation contract as
        `sync_fulfillment_for_shipment` -- OMS confirmation must never be
        rolled back, delayed, or blocked by a Shopify-side failure. The
        failure is recorded on `Order.shopify_confirmation_sync_status`/
        `shopify_confirmation_sync_error` so `POST /orders/{id}/shopify/
        retry-confirmation-sync` can retry just this half without
        re-pushing the (already-independently-idempotent) tag.
        """
        order = await self.orders.get_by_id(order_id)
        if order is None:
            raise NotFoundError("Order not found.")

        if not order.shopify_order_id:
            if order.shopify_confirmation_sync_status != ShopifySyncStatus.NOT_APPLICABLE:
                await self.orders.update(
                    order, shopify_confirmation_sync_status=ShopifySyncStatus.NOT_APPLICABLE
                )
                await self.session.commit()
            return order

        if order.shopify_confirmation_fulfillment_id:
            return order

        previous_status = order.shopify_confirmation_sync_status
        adapter = self._get_adapter()
        order_gid = f"gid://shopify/Order/{order.shopify_order_id}"

        try:
            fulfillment_order_id = await adapter.get_open_fulfillment_order_id(order_gid)
            if fulfillment_order_id is None:
                if previous_status == ShopifySyncStatus.FAILED:
                    await self.orders.update(
                        order,
                        shopify_confirmation_sync_status=ShopifySyncStatus.SYNCED,
                        shopify_confirmation_sync_error=(
                            "Shopify reports no remaining open fulfillment orders for this "
                            "order -- treating as already fulfilled (likely by an earlier "
                            "attempt whose result the OMS failed to record). The OMS does not "
                            "know this Fulfillment's id, so unconfirm cannot automatically "
                            "reverse it."
                        ),
                        shopify_confirmation_synced_at=datetime.now(UTC),
                    )
                else:
                    await self.orders.update(
                        order,
                        shopify_confirmation_sync_status=ShopifySyncStatus.NOT_APPLICABLE,
                    )
                await self.session.commit()
                return order

            fulfillment = await adapter.create_fulfillment(
                fulfillment_order_id=fulfillment_order_id,
                tracking_number=None,
                tracking_company=None,
                tracking_url=None,
                notify_customer=False,
            )
        except IntegrationError as exc:
            return await self._record_confirmation_failure(order, actor=actor, message=exc.message)
        except Exception as exc:  # noqa: BLE001 - never let an unexpected error block confirmation
            await self.session.rollback()
            order = await self.orders.get_by_id(order_id)
            assert order is not None
            return await self._record_confirmation_failure(
                order, actor=actor, message=f"Unexpected error syncing to Shopify: {exc}"
            )

        await self.orders.update(
            order,
            shopify_confirmation_fulfillment_id=fulfillment["id"],
            shopify_confirmation_sync_status=ShopifySyncStatus.SYNCED,
            shopify_confirmation_sync_error=None,
            shopify_confirmation_synced_at=datetime.now(UTC),
        )
        await self.audit.record(
            user=actor,
            action="order.shopify_confirmation_fulfillment_synced",
            entity_type="order",
            entity_id=str(order_id),
            new_value={"shopify_confirmation_fulfillment_id": fulfillment["id"]},
        )
        await self.session.commit()
        logger.info("shopify_confirmation_fulfillment_synced", order_id=str(order_id))
        return order

    async def reverse_confirmation_fulfillment(
        self, order_id: uuid.UUID, *, actor: User | None
    ) -> Order:
        """The inverse of `sync_confirmation_fulfillment` -- called by
        `OrderService.unconfirm_order` before it transitions the order
        back to PENDING. Removes `CONFIRMATION_TAG` (best-effort, same
        failure-isolation as the forward tag push: `tagsRemove` is a
        no-op for a tag that's already gone, so this is always safe to
        retry) and cancels EXACTLY the Fulfillment
        `shopify_confirmation_fulfillment_id` names -- never a generic
        "cancel whatever is open" call, so a real fulfillment created
        independently outside the OMS is never touched.

        Unlike every other method in this service, the fulfillment-
        cancellation half DOES raise (`IntegrationError`) on failure,
        deliberately: `unconfirm_order` must never transition the order
        to PENDING while Shopify still shows it Fulfilled, mirroring the
        existing rule that a failed Shiprocket cancellation also aborts
        the whole revert. The failure is still recorded first
        (`shopify_confirmation_sync_status=FAILED`) so a retry has
        something to act on and OMS confirmation history -- the order
        stays CONFIRMED, `confirmed_by_telecaller_id`/`confirmed_at`
        untouched -- is never corrupted by the failed attempt.

        Idempotent: `shopify_confirmation_fulfillment_id` already `None`
        (never synced, or a previous reversal already succeeded) ->
        returned unchanged, no Shopify call for the fulfillment half at
        all. `unconfirm_order` itself blocks BEFORE ever calling this
        when `shopify_confirmation_sync_status == SYNCED` but the id is
        `None` (the one case in `sync_confirmation_fulfillment` where the
        OMS knows Shopify was fulfilled but not the Fulfillment's real
        id) -- there is nothing this method could safely cancel then.
        """
        order = await self.orders.get_by_id(order_id)
        if order is None:
            raise NotFoundError("Order not found.")

        if not order.shopify_order_id:
            return order

        adapter = self._get_adapter()
        order_gid = f"gid://shopify/Order/{order.shopify_order_id}"
        try:
            await adapter.remove_order_tags(order_gid, [CONFIRMATION_TAG])
        except IntegrationError as exc:
            logger.warning(
                "shopify_confirmation_tag_removal_failed", order_id=str(order_id), error=exc.message
            )
        except Exception as exc:  # noqa: BLE001 - tag removal is best-effort, never blocking
            logger.warning(
                "shopify_confirmation_tag_removal_failed", order_id=str(order_id), error=str(exc)
            )

        if not order.shopify_confirmation_fulfillment_id:
            return order

        try:
            await adapter.cancel_fulfillment(order.shopify_confirmation_fulfillment_id)
        except IntegrationError as exc:
            truncated = exc.message[:_MAX_ERROR_MESSAGE_LENGTH]
            await self.orders.update(
                order,
                shopify_confirmation_sync_status=ShopifySyncStatus.FAILED,
                shopify_confirmation_sync_error=truncated,
            )
            await self.audit.record(
                user=actor,
                action="order.shopify_confirmation_fulfillment_reversal_failed",
                entity_type="order",
                entity_id=str(order_id),
                new_value={"error": truncated},
            )
            await self.session.commit()
            logger.warning(
                "shopify_confirmation_fulfillment_reversal_failed",
                order_id=str(order_id),
                error=truncated,
            )
            raise

        await self.orders.update(
            order,
            shopify_confirmation_fulfillment_id=None,
            shopify_confirmation_sync_status=ShopifySyncStatus.NOT_APPLICABLE,
            shopify_confirmation_sync_error=None,
            shopify_confirmation_synced_at=None,
        )
        await self.audit.record(
            user=actor,
            action="order.shopify_confirmation_fulfillment_reversed",
            entity_type="order",
            entity_id=str(order_id),
        )
        await self.session.commit()
        logger.info("shopify_confirmation_fulfillment_reversed", order_id=str(order_id))
        return order

    async def sync_shipping_address(self, order_id: uuid.UUID, *, actor: User | None) -> Order:
        """Pushes `Order.shipping_address` (the OMS's current, already-
        saved value) to the corresponding Shopify order via `orderUpdate`
        -- called by `OrderService.update_shipping_address` right after a
        Telecaller edit commits. Always OMS-first: by the time this runs,
        the OMS's own address has already been saved and committed, so a
        Shopify-side failure here can never leave the OMS out of sync
        with what the Telecaller actually entered.

        No "already synced" idempotency guard, unlike `sync_confirmation_
        fulfillment`/`sync_fulfillment_for_shipment`: `orderUpdate` sets
        Shopify's address in place rather than creating a new object, so
        calling this again (a retry, or simply saving the same address
        form a second time) just re-sends the same values -- always safe,
        never a duplicate. That also means there is no dedicated retry
        endpoint for this one: re-opening "Edit Address" and saving again
        (even unchanged) IS the retry.

        Never raises: same failure-isolation contract as every other
        outbound push here -- a Shopify failure is recorded on `Order.
        shipping_address_sync_status`/`shipping_address_sync_error` and
        never undoes or blocks the OMS address change that already
        committed. The caller (`OrderService.update_shipping_address`)
        returns this status in its response so the API/frontend can tell
        the difference between "saved and synced" and "saved, Shopify
        sync failed" rather than reporting blanket success.
        """
        order = await self.orders.get_by_id(order_id)
        if order is None:
            raise NotFoundError("Order not found.")

        if not order.shopify_order_id:
            if order.shipping_address_sync_status != ShopifySyncStatus.NOT_APPLICABLE:
                await self.orders.update(
                    order, shipping_address_sync_status=ShopifySyncStatus.NOT_APPLICABLE
                )
                await self.session.commit()
            return order

        if not order.shipping_address:
            return order

        adapter = self._get_adapter()
        order_gid = f"gid://shopify/Order/{order.shopify_order_id}"
        address_input = _to_shopify_address_input(order.shipping_address)

        try:
            await adapter.update_order_shipping_address(order_gid, address=address_input)
        except IntegrationError as exc:
            return await self._record_address_sync_failure(order, actor=actor, message=exc.message)
        except Exception as exc:  # noqa: BLE001 - never let an unexpected error corrupt state
            await self.session.rollback()
            order = await self.orders.get_by_id(order_id)
            assert order is not None
            return await self._record_address_sync_failure(
                order, actor=actor, message=f"Unexpected error syncing to Shopify: {exc}"
            )

        await self.orders.update(
            order,
            shipping_address_sync_status=ShopifySyncStatus.SYNCED,
            shipping_address_sync_error=None,
            shipping_address_synced_at=datetime.now(UTC),
        )
        await self.audit.record(
            user=actor,
            action="order.shopify_shipping_address_synced",
            entity_type="order",
            entity_id=str(order_id),
        )
        await self.session.commit()
        logger.info("shopify_shipping_address_synced", order_id=str(order_id))
        return order

    async def _record_address_sync_failure(
        self, order: Order, *, actor: User | None, message: str
    ) -> Order:
        truncated = message[:_MAX_ERROR_MESSAGE_LENGTH]
        await self.orders.update(
            order,
            shipping_address_sync_status=ShopifySyncStatus.FAILED,
            shipping_address_sync_error=truncated,
        )
        await self.audit.record(
            user=actor,
            action="order.shopify_shipping_address_sync_failed",
            entity_type="order",
            entity_id=str(order.id),
            new_value={"error": truncated},
        )
        await self.session.commit()
        logger.warning(
            "shopify_shipping_address_sync_failed", order_id=str(order.id), error=truncated
        )
        return order

    async def _record_confirmation_failure(
        self, order: Order, *, actor: User | None, message: str
    ) -> Order:
        truncated = message[:_MAX_ERROR_MESSAGE_LENGTH]
        await self.orders.update(
            order,
            shopify_confirmation_sync_status=ShopifySyncStatus.FAILED,
            shopify_confirmation_sync_error=truncated,
        )
        await self.audit.record(
            user=actor,
            action="order.shopify_confirmation_fulfillment_sync_failed",
            entity_type="order",
            entity_id=str(order.id),
            new_value={"error": truncated},
        )
        await self.session.commit()
        logger.warning(
            "shopify_confirmation_fulfillment_sync_failed", order_id=str(order.id), error=truncated
        )
        return order

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
