"""Shiprocket tracking-webhook processing: matches an inbound webhook
delivery to an existing OMS `Shipment` and applies the update. Kept
separate from `app.api.v1.webhooks.shiprocket` (the FastAPI layer, which
only handles HTTP concerns — token verification, JSON parsing, and
idempotent `WebhookEvent` ingestion via the generic `WebhookService`) so
the matching/update logic is unit-testable without going through HTTP,
the same split `app.tasks.webhook_processing` already keeps from the
Shopify webhook endpoint.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.integrations.entity_sync import (
    _resolve_order_by_channel_order_id,
    _resolve_shopify_order_by_api_order_id,
)
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.normalizer import (
    TRACKING_NORMALIZER,
    WEBHOOK_TRACKING_NORMALIZER,
    ShiprocketTrackingNormalizer,
    ShiprocketWebhookTrackingNormalizer,
    extract_tracking_events,
    extract_webhook_shipment_identifiers,
)
from app.integrations.shiprocket.sync import apply_tracking_event
from app.models.integration import IntegrationCode
from app.models.mixins import SourceSystem
from app.models.order import Order
from app.models.shipment import Shipment
from app.repositories.shipment import ShipmentRepository
from app.services.rto_service import RTOService
from app.services.shipment_service import ShipmentService

logger = get_logger(__name__)


class WebhookMatchResult:
    """Outcome of trying to match+apply one webhook delivery. Never
    raised for an ordinary "no safe match" outcome (spec: ack 200 and
    record the event for reconciliation rather than fabricate a match or
    error out) — only a genuine processing failure (a database error)
    propagates as an exception out of `apply_tracking_webhook`.
    """

    def __init__(
        self, *, matched: bool, match_strategy: str, shipment_id: uuid.UUID | None
    ) -> None:
        self.matched = matched
        self.match_strategy = match_strategy
        self.shipment_id = shipment_id


class ShiprocketWebhookService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shipments = ShipmentRepository(session)
        self.shipment_service = ShipmentService(session)
        self.rto_service = RTOService(session)

    async def apply_tracking_webhook(self, payload: dict[str, Any]) -> WebhookMatchResult:
        ids = extract_webhook_shipment_identifiers(payload)
        logger.info(
            "shiprocket_webhook_identifiers_extracted",
            has_awb=bool(ids["awb"]),
            has_shiprocket_shipment_id=bool(ids["shiprocket_shipment_id"]),
            has_shiprocket_order_id=bool(ids["shiprocket_order_id"]),
            has_channel_order_id=bool(ids["channel_order_id"]),
        )

        shipment, match_strategy = await self._find_or_create_shipment(ids)
        logger.info(
            "shiprocket_webhook_matching_attempt",
            matched=shipment is not None,
            match_strategy=match_strategy,
        )
        if shipment is None:
            return WebhookMatchResult(
                matched=False, match_strategy=match_strategy, shipment_id=None
            )

        normalizer: ShiprocketTrackingNormalizer | ShiprocketWebhookTrackingNormalizer
        nested_events = extract_tracking_events(payload)
        if nested_events:
            raw_events: list[dict[str, Any]] = nested_events
            normalizer = TRACKING_NORMALIZER
        else:
            raw_events = [payload]
            normalizer = WEBHOOK_TRACKING_NORMALIZER

        for raw_event in raw_events:
            normalized = normalizer.normalize_event(raw_event)
            if normalized["event_timestamp"] is None:
                # A live webhook's own arrival is a truthful stand-in for
                # "when did this happen" once no timestamp field could be
                # found under any known alias — unlike the historical poll
                # path (TRACKING_NORMALIZER via refresh_tracking), where a
                # missing timestamp is genuinely ambiguous and the event
                # is dropped rather than guessed.
                normalized = {**normalized, "event_timestamp": datetime.now(UTC)}
            await apply_tracking_event(
                self.session,
                shipment,
                normalized,
                shipment_service=self.shipment_service,
                rto_service=self.rto_service,
                source="shiprocket_webhook",
            )

        await self._apply_identifier_updates(shipment, ids)

        logger.info(
            "shiprocket_webhook_shipment_updated",
            shipment_id=str(shipment.id),
            match_strategy=match_strategy,
        )
        return WebhookMatchResult(
            matched=True, match_strategy=match_strategy, shipment_id=shipment.id
        )

    async def _find_or_create_shipment(
        self, ids: dict[str, str | None]
    ) -> tuple[Shipment | None, str]:
        """Preferred matching order (spec): AWB -> Shiprocket shipment id
        -> Shiprocket order id -> channel/Shopify order number. A step is
        skipped whenever its identifier isn't present in the payload, and
        nothing here ever falls back to name/phone/address matching
        (spec: never guess).
        """
        if ids["awb"]:
            shipment = await self.shipments.get_by_awb(ids["awb"])
            if shipment is not None:
                return shipment, "awb"

        if ids["shiprocket_shipment_id"]:
            shipment = await self.shipments.get_by_source_external_id(
                source_system=SourceSystem.SHIPROCKET,
                external_id=ids["shiprocket_shipment_id"],
            )
            if shipment is not None:
                return shipment, "shiprocket_shipment_id"

        order: Order | None = None
        match_strategy = "unmatched"
        if ids["channel_order_id"]:
            order = await _resolve_order_by_channel_order_id(self.session, ids["channel_order_id"])
            if order is not None:
                match_strategy = "channel_order_id"

        if order is None and ids["shiprocket_order_id"]:
            order = await self._resolve_order_via_orders_show(ids["shiprocket_order_id"])
            if order is not None:
                match_strategy = "shiprocket_order_id"

        if order is None:
            return None, "unmatched"

        existing = await self.shipments.list_for_order(order.id)
        if len(existing) == 1:
            return existing[0], match_strategy
        if len(existing) > 1:
            # Can't tell which of several shipments on this order the
            # webhook refers to from an order-level match alone. Never
            # guess — record for manual reconciliation instead of risking
            # an update to the wrong one.
            return None, "ambiguous_multiple_shipments_for_order"

        new_shipment_external_id = ids["shiprocket_shipment_id"] or ids["awb"]
        if new_shipment_external_id is None:
            # A real order, but nothing to key a new Shipment row on.
            return None, "order_resolved_no_shipment_identifier"

        # A real, resolved OMS order with no Shipment row yet (e.g. a
        # shipment Shopify's native Shiprocket channel created that
        # pull-sync hasn't picked up yet) — the same idempotent
        # create-or-update the pull-sync path already uses, keyed by real
        # Shiprocket identifiers just confirmed above, never a guess.
        shipment, _created = await self.shipment_service.upsert_synced_shipment(
            source_system=SourceSystem.SHIPROCKET,
            external_id=new_shipment_external_id,
            order_id=order.id,
            shiprocket_shipment_id=ids["shiprocket_shipment_id"],
            awb=ids["awb"],
        )
        return shipment, f"{match_strategy}_created_shipment"

    async def _resolve_order_via_orders_show(self, shiprocket_order_id: str) -> Order | None:
        """Same live `GET /orders/show/{id}` fallback
        `app.integrations.entity_sync._upsert_shipment` already uses for
        the pull-sync path, reused here rather than re-implemented so the
        two ingestion paths can't silently disagree about what counts as a
        match. Never raises — a lookup failure (network/permission) is
        logged and treated as "couldn't resolve," not "confirmed no match."

        Tries `channel_order_id` first (unchanged); when that fails to
        resolve, falls back to `api_order_id` -- confirmed live to equal
        `Order.external_id` for `source_system="shopify"` on orders
        created via Shiprocket's native Shopify channel connector, where
        `channel_order_id` is Shiprocket's own internal sequence number
        instead (see `_resolve_shopify_order_by_api_order_id`'s
        docstring). Reads it off the exact same response already
        fetched above -- no second Shiprocket API call.
        """
        adapter = get_adapter(IntegrationCode.SHIPROCKET)
        get_order = getattr(adapter, "get_order", None) if adapter else None
        if get_order is None:
            return None
        try:
            detail = await get_order(shiprocket_order_id)
        except IntegrationError as exc:
            logger.info(
                "shiprocket_webhook_orders_show_lookup_failed",
                shiprocket_order_id=shiprocket_order_id,
                error_type=exc.details.get("error_type"),
            )
            return None
        body = (detail.get("data") if isinstance(detail, dict) else None) or detail
        raw_channel_order_id = body.get("channel_order_id") if isinstance(body, dict) else None
        order = (
            await _resolve_order_by_channel_order_id(self.session, str(raw_channel_order_id))
            if raw_channel_order_id
            else None
        )
        if order is not None:
            return order

        raw_api_order_id = body.get("api_order_id") if isinstance(body, dict) else None
        order = await _resolve_shopify_order_by_api_order_id(self.session, raw_api_order_id)
        if order is not None:
            logger.info(
                "shiprocket_order_match_fallback",
                shiprocket_order_id=shiprocket_order_id,
                api_order_id=str(raw_api_order_id),
                source_system=SourceSystem.SHOPIFY,
                match_method="api_order_id",
            )
        return order

    async def _apply_identifier_updates(
        self, shipment: Shipment, ids: dict[str, str | None]
    ) -> None:
        """Fills in `awb`/`shiprocket_shipment_id` when the webhook
        supplied one this shipment didn't already have. Never overwrites
        an existing value with an absent one —
        `ShipmentService.update_shipment` already drops `None` fields
        (spec: never erase existing OMS data with a null/empty webhook
        value); a *different* non-null value (e.g. Shiprocket assigned a
        new AWB) is a legitimate update, not an erasure.
        """
        updates: dict[str, Any] = {}
        if ids["awb"] and shipment.awb != ids["awb"]:
            updates["awb"] = ids["awb"]
        if (
            ids["shiprocket_shipment_id"]
            and shipment.shiprocket_shipment_id != ids["shiprocket_shipment_id"]
        ):
            updates["shiprocket_shipment_id"] = ids["shiprocket_shipment_id"]
        if updates:
            await self.shipment_service.update_shipment(shipment.id, actor=None, **updates)
