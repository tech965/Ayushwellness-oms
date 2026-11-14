from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class FlipkartStatus(BaseModel):
    configured: bool
    missing: list[str]
    last_successful_fetch_at: datetime | None


class FlipkartOrderItem(BaseModel):
    """One shipment's order-item row. Fields verified against
    OMAPIRef.html's Search Shipments response -- `payment_type` has no
    documented field on this endpoint, so it is always `None` ("unavailable"
    in the UI), never guessed. `tracking_id` is likewise unavailable from
    this endpoint (only on the separate shipment-detail lookup) and is
    always `None` here.
    """

    order_id: str
    shipment_id: str
    order_date: str  # Amazon-style ISO passthrough; Flipkart's own format, not reparsed
    status: str
    product_name: str | None = None  # from subShipments[].packageTitle when present
    sku: str | None = None
    fsn: str | None = None
    quantity: int
    selling_price: Decimal | None = None
    payment_type: None = None  # always unavailable from this endpoint -- see docstring
    tracking_id: None = None  # always unavailable from this endpoint -- see docstring


class FlipkartOrdersPage(BaseModel):
    date_from: date | None
    date_to: date | None
    fetched_at: datetime
    items: list[FlipkartOrderItem]
    next_page_url: str | None
    has_more: bool
    unavailable: dict[str, str]
    warnings: list[str]
