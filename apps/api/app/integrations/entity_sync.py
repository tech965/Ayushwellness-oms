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
from datetime import UTC
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IntegrationError, NotFoundError
from app.core.logging import get_logger
from app.integrations.registry import get_adapter
from app.models.integration import IntegrationCode
from app.repositories.order import OrderRepository
from app.repositories.shipment import ShipmentRepository
from app.services.customer_service import CustomerService
from app.services.ndr_service import NDRService
from app.services.order_service import OrderService
from app.services.product_service import ProductService
from app.services.shipment_service import ShipmentService

logger = get_logger(__name__)

UpsertHandler = Callable[[AsyncSession, dict[str, Any]], Awaitable[tuple[Any, bool]]]


async def _upsert_customer(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await CustomerService(session).upsert_synced_customer(**data)


async def _upsert_product(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await ProductService(session).upsert_synced_product(**data)


async def _upsert_order(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await OrderService(session).upsert_synced_order(**data)


async def _upsert_ndr(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    return await NDRService(session).upsert_synced_ndr(**data)


async def _resolve_order_by_channel_order_id(
    session: AsyncSession, channel_order_id: str | None
) -> Any | None:
    """`Order.order_number` is always stored with a leading `#` (Shopify's
    `name` field). Real live evidence this engagement: `channel_order_id`
    comes back *with* the `#` when this OMS created the Shiprocket order
    itself (`ShiprocketOrderPushNormalizer` sends `order.order_number`
    verbatim), but *without* it for shipments created outside this OMS
    (e.g. Shopify's native Shiprocket channel connection). Trying both
    forms — never inventing a third — covers both real, confirmed cases.
    """
    if not channel_order_id:
        return None
    order = await OrderRepository(session).get_by_order_number(channel_order_id)
    if order is not None:
        return order
    if not channel_order_id.startswith("#"):
        return await OrderRepository(session).get_by_order_number(f"#{channel_order_id}")
    return None


async def _upsert_shipment(session: AsyncSession, data: dict[str, Any]) -> tuple[Any, bool]:
    """Round 13 fix: a Shiprocket shipment this OMS already knows about
    (every one ever created went through `ShiprocketOperationsService.
    create_shipment_for_order` — the only code path that creates a
    `Shipment` row) already has a correct `order_id`, set the day it was
    created, from the real OMS order it was created *for* — never
    derived from anything Shiprocket returns. Its `(source_system,
    external_id)` — `external_id` being that shipment's own Shiprocket
    `id`, confirmed live to be exactly what `ShiprocketShipmentNormalizer`
    already reads (`raw.get("id")`) — is the reliable, pre-existing
    identity, so it's checked *first*, before any order lookup is even
    attempted.

    Round 14 fix: for a shipment with NO existing `Shipment` row (a real,
    common case — most Shiprocket shipments in this account were never
    created by this OMS), `channel_order_id` from `/shipments` is
    confirmed live to always be `None` — the field simply isn't on that
    endpoint. `GET /orders/show/{order_id}` *does* return it reliably
    (confirmed live), so that's tried next, using `shiprocket_order_id`
    (Shiprocket's own numeric order id, present on every `/shipments`
    record) to fetch it. `api_order_id` from that same endpoint was
    tested and confirmed live to be unreliable (`None` for at least one
    real OMS-created order) — deliberately not used.

    Round 15 fix: that `/orders/show` call is only skipped as a
    performance boundary — never a matching decision — when
    `shiprocket_created_at` is confirmed older than this OMS's earliest
    ever synced `Order.order_datetime`. Confirmed live this engagement: a
    shipment that old can never resolve to a real OMS order (its order
    predates this OMS's Shopify sync coverage entirely), so the live call
    is pure waste — and at real account scale (thousands of historical
    shipments), it was also the direct cause of hitting Shiprocket's rate
    limit on every scheduled sync. `None` on either side of the
    comparison (unparseable timestamp, or no orders synced yet) always
    means "don't skip" — the boundary can only ever narrow which records
    get an extra check, never cause one to be silently skipped when it
    could have matched.

    A shipment that still can't be resolved raises `NotFoundError`,
    exactly like `NDRService.upsert_synced_ndr` already does for an
    unmatched AWB (spec §16, "do not invent NDR/shipment data") —
    `SyncService._run_entity_sync`'s existing per-record try/except
    records it as a `SyncError` and moves on; the job still lands
    PARTIAL, not FAILED, and nothing fabricated ever reaches the
    database. Every resolution attempt is logged (see
    `shiprocket_shipment_order_resolution` below), including whatever
    `channel_order_id` was actually found even when it didn't match, so a
    failure's exact reason is always visible, never silent.
    """
    source_system = data.get("source_system")
    external_id = data.get("external_id")
    existing = (
        await ShipmentRepository(session).get_by_source_external_id(
            source_system=source_system, external_id=external_id
        )
        if source_system and external_id
        else None
    )

    if existing is not None:
        data.pop("channel_order_id", None)  # not needed -- order_id is already known
        data.pop("shiprocket_order_id", None)
        data.pop("shiprocket_created_at", None)
        return await ShipmentService(session).upsert_synced_shipment(
            order_id=existing.order_id, **data
        )

    channel_order_id = data.pop("channel_order_id", None)
    shiprocket_order_id = data.pop("shiprocket_order_id", None)
    shiprocket_created_at = data.pop("shiprocket_created_at", None)

    order = await _resolve_order_by_channel_order_id(session, channel_order_id)
    match_strategy = "shipments_channel_order_id" if order is not None else None
    skip_reason = None

    if order is None and shiprocket_order_id:
        earliest_order_datetime = await OrderRepository(session).get_earliest_order_datetime()
        # `_parse_datetime` (Shiprocket's side) returns a naive datetime;
        # `Order.order_datetime` (via `AwareDateTime`) always loads
        # tz-aware. Comparing them directly raises TypeError -- caught by
        # `_run_entity_sync`'s per-record try/except, which would
        # silently misreport a crash as an ordinary "no match" SyncError.
        # Normalizing to UTC-aware here only, not in the shared
        # `_parse_datetime` helper, keeps this fix local to this one
        # comparison rather than changing behavior for every other caller
        # of that helper (NDR/tracking timestamps).
        comparable_created_at = shiprocket_created_at
        if comparable_created_at is not None and comparable_created_at.tzinfo is None:
            comparable_created_at = comparable_created_at.replace(tzinfo=UTC)
        predates_oms_coverage = bool(
            comparable_created_at
            and earliest_order_datetime
            and comparable_created_at < earliest_order_datetime
        )
        if predates_oms_coverage:
            skip_reason = "shiprocket_created_at predates earliest OMS order_datetime"
        else:
            adapter = get_adapter(IntegrationCode.SHIPROCKET)
            get_order = getattr(adapter, "get_order", None)
            if get_order is not None:
                try:
                    order_detail = await get_order(shiprocket_order_id)
                except IntegrationError as exc:
                    # Distinguish "we couldn't even check" from "we
                    # checked and there's genuinely no match" — collapsing
                    # both into the same generic NotFoundError message
                    # (the previous behavior) made a permanent,
                    # account-wide permission block on this endpoint
                    # indistinguishable from thousands of individually
                    # unmatched shipments, both in logs and in every
                    # SyncError's message text.
                    order_detail = None
                    skip_reason = (
                        f"orders_show_lookup_failed: "
                        f"{exc.details.get('error_type', 'unknown_error')} — {exc.message}"
                    )
                body = (
                    order_detail.get("data") if isinstance(order_detail, dict) else None
                ) or order_detail
                raw_resolved = body.get("channel_order_id") if isinstance(body, dict) else None
                fetched_channel_order_id = str(raw_resolved) if raw_resolved is not None else None
                if fetched_channel_order_id is not None:
                    channel_order_id = fetched_channel_order_id
                order = await _resolve_order_by_channel_order_id(session, fetched_channel_order_id)
                if order is not None:
                    match_strategy = "orders_show_channel_order_id"

    logger.info(
        "shiprocket_shipment_order_resolution",
        shiprocket_shipment_id=external_id,
        shiprocket_order_id=shiprocket_order_id,
        channel_order_id=channel_order_id,
        matched=order is not None,
        match_strategy=match_strategy,
        skip_reason=skip_reason,
        matched_order_id=str(order.id) if order is not None else None,
    )

    if order is None:
        # `skip_reason` distinguishes a genuine no-match (None here) from
        # "we couldn't actually check" (a predates-OMS-history skip, or an
        # /orders/show failure — including a permission block) — visible
        # in the SyncError's own message text, not just the structured log
        # above, since that's what a human reviewing Sync History sees.
        reason_suffix = f" [{skip_reason}]" if skip_reason else ""
        raise NotFoundError(
            "No OMS order found for Shiprocket shipment "
            f"(channel_order_id={channel_order_id!r}).{reason_suffix}"
        )
    return await ShipmentService(session).upsert_synced_shipment(order_id=order.id, **data)


ENTITY_UPSERT_HANDLERS: dict[str, UpsertHandler] = {
    "customers": _upsert_customer,
    "products": _upsert_product,
    "orders": _upsert_order,
    "ndr": _upsert_ndr,
    "shipments": _upsert_shipment,
}
