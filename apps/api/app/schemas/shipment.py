from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AddressValidationStatus,
    NDRStatus,
    PaymentStatus,
    PaymentType,
    RTOStatus,
    ShipmentDelayStatus,
    ShipmentStatus,
    ShopifySyncStatus,
)


class ShipmentCreateRequest(BaseModel):
    order_id: uuid.UUID
    awb: str | None = None
    courier_id: uuid.UUID | None = None
    expected_delivery_date: datetime | None = None


class ShipmentUpdateRequest(BaseModel):
    courier_id: uuid.UUID | None = None
    current_status: ShipmentStatus | None = None
    expected_delivery_date: datetime | None = None
    current_location: str | None = None


class ShipmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    shiprocket_shipment_id: str | None
    awb: str | None
    courier_id: uuid.UUID | None
    current_status: ShipmentStatus
    delay_status: ShipmentDelayStatus
    ndr_status: NDRStatus | None
    rto_status: RTOStatus | None
    pickup_date: datetime | None
    expected_delivery_date: datetime | None
    actual_delivery_date: datetime | None
    current_location: str | None
    last_tracking_update_at: datetime | None
    source_system: str | None
    shopify_fulfillment_id: str | None
    shopify_sync_status: ShopifySyncStatus
    shopify_sync_error: str | None
    shopify_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ShipmentListResponse(ShipmentResponse):
    """`ShipmentResponse` plus the denormalized fields the Shipments list
    (`GET /shipments`) needs to render order/customer/courier/telecaller
    columns without an N+1 request per row — same pattern as
    `OrderListResponse`/`ShipmentQueueRowResponse`. Only `list_shipments`
    returns this; `GET /shipments/{id}` still returns plain
    `ShipmentResponse`, which already has its own order/customer context
    from the order detail page it's shown alongside.
    """

    order_number: str | None = None
    customer_name: str | None = None
    payment_type: PaymentType | None = None
    confirmed_by_telecaller_name: str | None = None
    courier_name: str | None = None


class ShipmentEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_id: uuid.UUID
    external_event_id: str | None
    status: str
    location: str | None
    event_timestamp: datetime
    description: str | None
    courier_name: str | None
    source: str
    created_at: datetime


class ShipmentEventCreateRequest(BaseModel):
    external_event_id: str | None = None
    status: str = Field(min_length=1, max_length=100)
    location: str | None = None
    event_timestamp: datetime
    description: str | None = None
    courier_name: str | None = None


class ShipmentQueueRowResponse(BaseModel):
    """One row of the Shipment Queue — confirmed orders awaiting shipment
    processing (`OrderRepository.shipment_queue_query`). Denormalized the
    same way `AssignedOrderResponse`/`OrderListResponse` already are, so
    the table renders without an N+1 request per row. `shipment_*` fields
    are `None` when no shipment has been created for this order yet.
    """

    order_id: uuid.UUID
    order_number: str
    customer_name: str | None
    customer_phone: str | None
    item_summary: str | None
    sku_summary: str | None
    total_quantity: int
    total_amount: Decimal
    payment_type: PaymentType
    payment_status: PaymentStatus
    confirmed_at: datetime | None
    confirmed_by_telecaller_id: uuid.UUID | None
    confirmed_by_telecaller_name: str | None
    shipment_id: uuid.UUID | None = None
    shipment_status: ShipmentStatus | None = None
    awb: str | None = None
    courier_name: str | None = None
    # Outbound Shopify push status for this row's shipment -- lets the
    # queue table offer "Retry Shopify Sync" directly, without opening
    # the shipment. `None` exactly when `shipment_id` is `None`.
    shopify_sync_status: ShopifySyncStatus | None = None
    # The real, numeric Shiprocket order id for this shipment (see
    # `app.services.shiprocket_service.shiprocket_order_id`) -- `None`
    # when the OMS has no reliably-stored one, including whenever
    # `shipment_id` is `None`. Shiprocket has no supported deep-link
    # filter for its "Ready to Ship" page, so the Fulfillment Queue's
    # "Process Shipment"/"Ship Order" actions open that page plain and
    # copy this id to the clipboard, instead of calling any Shiprocket
    # create-shipment API.
    shiprocket_order_id: str | None = None
    # Address validation (Shiprocket-style confidence check) -- see
    # `Order.shipping_address_validation_status` in app/models/order.py.
    # Sourced directly from the SAME `Order` row this whole query already
    # joins in (`OrderRepository.shipment_queue_query`), so showing this
    # on the Fulfillment Queue costs zero extra queries per row -- never
    # `None` when a `shipping_address` exists but hasn't been validated
    # yet; that case is `status=None` (shown as "Validation pending"),
    # not omitted.
    shipping_address_validation_status: AddressValidationStatus | None = None
    shipping_address_validation_score: int | None = None
    shipping_address_validation_reason: str | None = None


class ShipmentSummaryResponse(BaseModel):
    confirmed_awaiting_shipment: int = 0
    total_shipments: int = 0
    pending: int = 0
    picked_up: int = 0
    in_transit: int = 0
    out_for_delivery: int = 0
    delivered: int = 0
    ndr: int = 0
    rto: int = 0
    cancelled: int = 0
    cod: int = 0
    prepaid: int = 0
    todays_shipments: int = 0


class ShipmentStatusBreakdownItem(BaseModel):
    status: str
    count: int


class DailyShipmentTrendPoint(BaseModel):
    date: str
    created: int
    delivered: int


class TelecallerShipmentStats(BaseModel):
    telecaller_id: uuid.UUID
    telecaller_name: str
    confirmed: int
    shipped: int
    delivered: int
    ndr: int = 0
    rto: int = 0


class BulkShipOrdersRequest(BaseModel):
    order_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class BulkShipOrderResult(BaseModel):
    order_id: uuid.UUID
    success: bool
    message: str | None
    shipment_id: uuid.UUID | None = None


class BulkShipOrdersResponse(BaseModel):
    shipped_count: int
    failed_count: int
    results: list[BulkShipOrderResult]


class BulkShipValidationResult(BaseModel):
    """One order's dry-run eligibility result from `ShiprocketOperations
    Service.validate_shipment_eligibility` -- never creates anything, just
    classifies. `reason` is `None` exactly when `ready` is `True`.
    """

    order_id: uuid.UUID
    ready: bool
    reason: str | None = None


class ShipmentAnalyticsResponse(BaseModel):
    status_breakdown: list[ShipmentStatusBreakdownItem]
    # % of orders that ever reached CONFIRMED (or any status after it —
    # the state machine is strictly monotonic, so PROCESSING/PACKED/
    # SHIPPED/DELIVERED all imply "was confirmed") AND were never
    # fulfilled directly through Shopify (excluded — those never enter
    # this OMS's own shipment pipeline at all) whose shipment has moved
    # beyond the queue (PICKED_UP or later). See
    # `OrderRepository.confirmation_to_shipment_stats`'s docstring.
    confirmation_to_shipment_rate: float = 0.0
    daily_trend: list[DailyShipmentTrendPoint]
    telecaller_stats: list[TelecallerShipmentStats]
