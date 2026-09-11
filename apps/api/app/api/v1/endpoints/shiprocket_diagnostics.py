"""TEMPORARY DIAGNOSTIC ENDPOINT -- REMOVE AFTER THE #AWL95498 "AWB
assigned but absent from Shiprocket's Ready to Ship page" investigation
is closed.

Read-only, ADMIN-only (`current_user.is_superuser` — deliberately NOT a
regular permission code, so it can never be granted via a role). Never
creates a Shiprocket order (no `orders/create/adhoc` call anywhere in
this file or anything it calls), never assigns/reassigns an AWB, never
changes courier, never writes to the database — this module contains no
`session.commit()`/`session.add()`/repository `.update()`/`.create()`
call at all. Calls only the EXISTING, already-vetted Shiprocket adapter
read methods (`get_tracking`, `fetch_incremental("shipments", ...)`,
`get_order`) — no new Shiprocket API surface is introduced, and no
credential, token, or header is ever included in the response.

Given an OMS order number or OMS shipment id, reports:
  - every identifier the OMS has stored for it,
  - Shiprocket's own tracking-API answer for its AWB (`GET /courier/
    track/awb/{awb}`),
  - a best-effort cross-check against Shiprocket's own `/shipments` list
    for the same `shiprocket_shipment_id`,
  - a best-effort cross-check against `GET /orders/show/{id}` for the
    same `shiprocket_order_id`,
  - a best-effort classification of "what Shiprocket currently says".

The classification is explicitly best-effort: this codebase's own
Shiprocket integration has never been exercised against a real account
(see `docs/integrations/shiprocket.md`), so the exact vocabulary
Shiprocket uses for "Ready to Ship" vs "Pickup Scheduled" vs
"Manifested" in a tracking response's raw status text is not confirmed
— the raw text is always included in the response so a human can judge
for themselves, never hidden behind the classification alone.

TO REMOVE ONCE THE INVESTIGATION IS CLOSED:
  1. Delete this file.
  2. Remove its `include_router(...)` block from `app/api/v1/router.py`
     (and the `shiprocket_diagnostics` import above it).
  3. Delete `tests/test_shiprocket_diagnostics.py`.
No other file references this module — it introduces no shared state,
no schema/model change, no migration.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IntegrationError
from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.normalizer import TRACKING_NORMALIZER, extract_tracking_events
from app.models.auth import User
from app.models.integration import IntegrationCode
from app.models.order import Order
from app.models.shipment import Shipment
from app.repositories.order import OrderRepository
from app.repositories.shipment import ShipmentRepository
from app.schemas.response import ApiResponse
from app.services.courier_service import CourierService
from app.services.shiprocket_service import shiprocket_order_id as derive_shiprocket_order_id

router = APIRouter()

# Bounded the same way `app.services.shiprocket_service.
# locate_shiprocket_orders` bounds its own live `/shipments` scan — a
# diagnostic must never turn into an unbounded crawl.
_SHIPMENTS_SCAN_MAX_PAGES = 3
_SHIPMENTS_SCAN_MAX_CANDIDATES = 60

ShiprocketStatusBucket = Literal[
    "READY_TO_SHIP",
    "PICKUP_SCHEDULED",
    "MANIFESTED",
    "IN_TRANSIT",
    "DELIVERED",
    "RTO",
    "CANCELLED",
    "NOT_FOUND",
    "UNKNOWN",
]


async def _require_superuser(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin (superuser) access required.")
    return current_user


class OmsIdentifiers(BaseModel):
    order_id: uuid.UUID
    order_number: str
    shipment_id: uuid.UUID
    shiprocket_shipment_id: str | None
    shiprocket_order_id: str | None
    awb: str | None
    courier_id: uuid.UUID | None
    courier_name: str | None
    current_status: str
    raw_external_payload_keys: list[str]


class TrackingLookupResult(BaseModel):
    called: bool
    error: str | None = None
    raw_status_text: str | None = None
    tracking_top_level_fields: dict[str, Any] | None = None
    latest_event: dict[str, Any] | None = None
    event_count: int = 0


class ShipmentsScanResult(BaseModel):
    called: bool
    found: bool = False
    error: str | None = None
    pages_scanned: int = 0
    candidates_examined: int = 0
    raw_status_text: str | None = None
    raw_courier_name: str | None = None
    raw_awb: str | None = None


class OrderShowResult(BaseModel):
    called: bool
    found: bool = False
    error: str | None = None
    blocked_reason: str | None = None
    raw_status_fields: dict[str, Any] | None = None


class ShiprocketDiagnosticResponse(BaseModel):
    oms: OmsIdentifiers
    tracking: TrackingLookupResult
    shipments_scan: ShipmentsScanResult
    order_show: OrderShowResult
    classification: ShiprocketStatusBucket
    classification_basis: str
    comparison_notes: list[str]


def _classify(
    *, has_tracking_data: bool, has_shipments_match: bool, raw_status_text: str | None
) -> tuple[ShiprocketStatusBucket, str]:
    """Best-effort only -- see this module's docstring. Matches against
    Shiprocket's commonly-documented status vocabulary; an unrecognized
    or empty status with SOME data found is `UNKNOWN` (never guessed as
    a specific bucket), and no data found at all from either read path
    is `NOT_FOUND`.
    """
    if not has_tracking_data and not has_shipments_match:
        return "NOT_FOUND", "Neither the tracking API nor the /shipments scan returned a match."

    text = (raw_status_text or "").strip().upper()
    if not text:
        return "UNKNOWN", "A match was found, but no readable status text was present."
    if "UNDELIVERED" in text or "NDR" in text:
        return "UNKNOWN", f"Raw status '{raw_status_text}' indicates an NDR/undelivered state."
    if "DELIVERED" in text:
        return "DELIVERED", f"Raw status '{raw_status_text}' contains 'DELIVERED'."
    if "RTO" in text:
        return "RTO", f"Raw status '{raw_status_text}' contains 'RTO'."
    if "CANCEL" in text:
        return "CANCELLED", f"Raw status '{raw_status_text}' contains 'CANCEL'."
    if any(k in text for k in ("TRANSIT", "OUT FOR DELIVERY", "SHIPPED", "PICKED UP")):
        return "IN_TRANSIT", f"Raw status '{raw_status_text}' indicates movement/transit."
    if "MANIFEST" in text:
        return "MANIFESTED", f"Raw status '{raw_status_text}' contains 'MANIFEST'."
    if "PICKUP" in text:
        return "PICKUP_SCHEDULED", f"Raw status '{raw_status_text}' contains 'PICKUP'."
    if "READY" in text or "NEW" in text:
        return "READY_TO_SHIP", f"Raw status '{raw_status_text}' suggests pre-pickup/Ready to Ship."
    return "UNKNOWN", f"Raw status '{raw_status_text}' did not match any known bucket."


@router.get("/shipment-status", response_model=ApiResponse[ShiprocketDiagnosticResponse])
async def diagnose_shiprocket_shipment_status(
    order_number: str | None = Query(default=None),
    shipment_id: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(_require_superuser),
) -> ApiResponse[ShiprocketDiagnosticResponse]:
    """TEMPORARY. See this module's docstring. Exactly one of
    `order_number`/`shipment_id` must be given. `order_number` accepts
    either the bare number (`AWL95498`) or the stored form with its
    leading `#` (`#AWL95498`).
    """
    if bool(order_number) == bool(shipment_id):
        raise HTTPException(
            status_code=422, detail="Provide exactly one of order_number or shipment_id."
        )

    orders = OrderRepository(session)
    shipments = ShipmentRepository(session)

    order: Order | None = None
    shipment: Shipment | None = None

    if shipment_id is not None:
        shipment = await shipments.get_by_id(shipment_id)
        if shipment is None:
            raise HTTPException(status_code=404, detail="Shipment not found.")
        order = await orders.get_by_id(shipment.order_id)
    else:
        assert order_number is not None
        candidates = (
            [order_number] if order_number.startswith("#") else [order_number, f"#{order_number}"]
        )
        for candidate in candidates:
            order = await orders.get_by_order_number(candidate)
            if order is not None:
                break
        if order is None:
            raise HTTPException(
                status_code=404, detail=f"No order found matching '{order_number}'."
            )
        existing = await shipments.list_for_order(order.id)
        shipment = max(existing, key=lambda s: s.created_at) if existing else None
        if shipment is None:
            raise HTTPException(
                status_code=404, detail=f"Order '{order_number}' has no Shipment row on file."
            )

    assert order is not None and shipment is not None

    courier_name: str | None = None
    if shipment.courier_id is not None:
        try:
            courier = await CourierService(session).get_courier(shipment.courier_id)
            courier_name = courier.name
        except Exception:  # noqa: BLE001 - diagnostic display only, never fatal
            courier_name = None

    resolved_shiprocket_order_id = derive_shiprocket_order_id(shipment)
    oms_identifiers = OmsIdentifiers(
        order_id=order.id,
        order_number=order.order_number,
        shipment_id=shipment.id,
        shiprocket_shipment_id=shipment.shiprocket_shipment_id,
        shiprocket_order_id=resolved_shiprocket_order_id,
        awb=shipment.awb,
        courier_id=shipment.courier_id,
        courier_name=courier_name,
        current_status=shipment.current_status.value,
        raw_external_payload_keys=(
            sorted(shipment.raw_external_payload.keys()) if shipment.raw_external_payload else []
        ),
    )

    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    if not isinstance(adapter, ShiprocketAdapter):
        not_configured = TrackingLookupResult(called=False, error="Shiprocket is not configured.")
        return ApiResponse(
            data=ShiprocketDiagnosticResponse(
                oms=oms_identifiers,
                tracking=not_configured,
                shipments_scan=ShipmentsScanResult(
                    called=False, error="Shiprocket is not configured."
                ),
                order_show=OrderShowResult(called=False, error="Shiprocket is not configured."),
                classification="UNKNOWN",
                classification_basis="Shiprocket adapter is not configured in this environment.",
                comparison_notes=[
                    "SHIPROCKET_EMAIL/SHIPROCKET_PASSWORD are not both set here -- no live "
                    "Shiprocket call was possible."
                ],
            )
        )

    # --- 1. GET /courier/track/awb/{awb} (existing adapter.get_tracking) ---
    tracking_result = TrackingLookupResult(called=False)
    latest_event_normalized: dict[str, Any] | None = None
    if shipment.awb:
        tracking_result.called = True
        try:
            raw = await adapter.get_tracking(shipment.awb)
        except IntegrationError as exc:
            tracking_result.error = exc.message
        else:
            tracking_data = raw.get("tracking_data") or raw
            top_level = {
                k: v
                for k, v in tracking_data.items()
                if k not in ("shipment_track_activities", "shipment_track")
            }
            tracking_result.tracking_top_level_fields = top_level or None
            events = [TRACKING_NORMALIZER.normalize_event(e) for e in extract_tracking_events(raw)]
            tracking_result.event_count = len(events)
            if events:
                dated = [e for e in events if e["event_timestamp"] is not None]
                latest_event_normalized = (
                    max(dated, key=lambda e: e["event_timestamp"]) if dated else events[0]
                )
                tracking_result.latest_event = {
                    "status": latest_event_normalized["status"],
                    "description": latest_event_normalized["description"],
                    "location": latest_event_normalized["location"],
                    "event_timestamp": (
                        latest_event_normalized["event_timestamp"].isoformat()
                        if latest_event_normalized["event_timestamp"]
                        else None
                    ),
                    "courier_name": latest_event_normalized["courier_name"],
                }
                tracking_result.raw_status_text = latest_event_normalized["status"]
            elif isinstance(top_level.get("shipment_status"), str):
                tracking_result.raw_status_text = top_level["shipment_status"]
    else:
        tracking_result.error = "This OMS shipment has no AWB on file."

    # --- 2. Bounded /shipments scan for the same shiprocket_shipment_id --
    scan_result = ShipmentsScanResult(called=False)
    if shipment.shiprocket_shipment_id:
        scan_result.called = True
        since = shipment.created_at - timedelta(days=1)
        cursor: str | None = None
        try:
            for _page_num in range(_SHIPMENTS_SCAN_MAX_PAGES):
                page = await adapter.fetch_incremental(
                    "shipments", since=since, cursor=cursor, limit=50
                )
                scan_result.pages_scanned += 1
                for raw_node in page.nodes:
                    if scan_result.candidates_examined >= _SHIPMENTS_SCAN_MAX_CANDIDATES:
                        break
                    scan_result.candidates_examined += 1
                    if str(raw_node.get("id")) == str(shipment.shiprocket_shipment_id):
                        scan_result.found = True
                        scan_result.raw_status_text = raw_node.get("status")
                        scan_result.raw_courier_name = raw_node.get("courier_name")
                        scan_result.raw_awb = raw_node.get("awb")
                        break
                if scan_result.found or not page.has_more:
                    break
                if scan_result.candidates_examined >= _SHIPMENTS_SCAN_MAX_CANDIDATES:
                    break
                cursor = page.next_cursor
        except IntegrationError as exc:
            scan_result.error = exc.message
    else:
        scan_result.error = "This OMS shipment has no shiprocket_shipment_id on file."

    # --- 3. GET /orders/show/{shiprocket_order_id} (best-effort) ---------
    order_show_result = OrderShowResult(called=False)
    if resolved_shiprocket_order_id:
        order_show_result.called = True
        try:
            raw_order = await adapter.get_order(resolved_shiprocket_order_id)
        except IntegrationError as exc:
            if exc.details.get("orders_show_blocked"):
                order_show_result.blocked_reason = exc.message
            else:
                order_show_result.error = exc.message
        else:
            order_show_result.found = True
            data_field = raw_order.get("data")
            data: dict[str, Any] = data_field if isinstance(data_field, dict) else raw_order
            order_show_result.raw_status_fields = {
                k: v
                for k, v in data.items()
                if "status" in k.lower() or "channel" in k.lower() or "courier" in k.lower()
            } or None
    else:
        order_show_result.error = "No Shiprocket order_id could be derived from this shipment."

    classification, basis = _classify(
        has_tracking_data=bool(tracking_result.event_count or tracking_result.raw_status_text),
        has_shipments_match=scan_result.found,
        raw_status_text=tracking_result.raw_status_text or scan_result.raw_status_text,
    )

    comparison_notes: list[str] = []
    if tracking_result.raw_status_text and scan_result.raw_status_text:
        tracking_upper = tracking_result.raw_status_text.strip().upper()
        scan_upper = scan_result.raw_status_text.strip().upper()
        if tracking_upper != scan_upper:
            comparison_notes.append(
                f"Tracking API status ('{tracking_result.raw_status_text}') and the /shipments "
                f"scan status ('{scan_result.raw_status_text}') do NOT match -- Shiprocket's own "
                "API and dashboard may disagree, or these were read at different moments."
            )
        else:
            comparison_notes.append("Tracking API and /shipments scan report the same status.")
    if oms_identifiers.awb and not tracking_result.called:
        comparison_notes.append("OMS has an AWB on file but the tracking call was never made.")
    if not scan_result.called and not tracking_result.called:
        comparison_notes.append(
            "No live Shiprocket read succeeded -- see the `error` fields above."
        )

    return ApiResponse(
        data=ShiprocketDiagnosticResponse(
            oms=oms_identifiers,
            tracking=tracking_result,
            shipments_scan=scan_result,
            order_show=order_show_result,
            classification=classification,
            classification_basis=basis,
            comparison_notes=comparison_notes,
        )
    )
