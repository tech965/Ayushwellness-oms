"""Shiprocket <-> OMS field mapping. Pure functions — no DB, no network."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from app.integrations.shiprocket.normalizer import (
    NDR_NORMALIZER,
    SHIPMENT_NORMALIZER,
    TRACKING_NORMALIZER,
    ShiprocketOrderPushNormalizer,
    normalize_payment_method,
    normalize_shipment_status,
)
from app.models.enums import PaymentType, ShipmentStatus


# 11. Status mapping
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("NEW", ShipmentStatus.PENDING),
        ("PICKED UP", ShipmentStatus.PICKED_UP),
        ("IN TRANSIT", ShipmentStatus.IN_TRANSIT),
        ("in transit", ShipmentStatus.IN_TRANSIT),  # case-insensitive
        ("OUT FOR DELIVERY", ShipmentStatus.OUT_FOR_DELIVERY),
        ("DELIVERED", ShipmentStatus.DELIVERED),
        ("UNDELIVERED", ShipmentStatus.NDR),
        ("CANCELLED", ShipmentStatus.CANCELLED),
        ("RTO INITIATED", ShipmentStatus.RTO_INITIATED),
        ("RTO DELIVERED", ShipmentStatus.RTO_DELIVERED),
    ],
)
def test_shipment_status_mapping_is_documented(raw_status, expected) -> None:
    assert normalize_shipment_status(raw_status) == expected


def test_unknown_shipment_status_returns_none_instead_of_guessing() -> None:
    # spec §14: unknown statuses must be handled safely, never crash and
    # never silently invent a mapping.
    assert normalize_shipment_status("SOME BRAND NEW STATUS SHIPROCKET ADDS LATER") is None
    assert normalize_shipment_status(None) is None


# 10. Tracking normalization
def test_tracking_event_normalization_maps_core_fields() -> None:
    raw = {
        "id": 555,
        "status": "IN TRANSIT",
        "location": "Mumbai Hub",
        "date": "2026-01-05 14:30:00",
        "activity": "Shipment in transit",
        "courier_name": "Delhivery",
    }
    normalized = TRACKING_NORMALIZER.normalize_event(raw)

    assert normalized["external_event_id"] == "555"
    assert normalized["status"] == "IN TRANSIT"
    assert normalized["mapped_status"] == ShipmentStatus.IN_TRANSIT
    assert normalized["location"] == "Mumbai Hub"
    assert normalized["event_timestamp"] == datetime(2026, 1, 5, 14, 30, 0)
    assert normalized["courier_name"] == "Delhivery"


def test_tracking_event_with_unparseable_date_has_no_timestamp() -> None:
    normalized = TRACKING_NORMALIZER.normalize_event({"status": "IN TRANSIT", "date": "not-a-date"})
    assert normalized["event_timestamp"] is None


def test_tracking_event_with_unknown_status_still_normalizes_safely() -> None:
    normalized = TRACKING_NORMALIZER.normalize_event(
        {"status": "SOME FUTURE STATUS", "date": "2026-01-01 00:00:00"}
    )
    assert normalized["mapped_status"] is None
    assert normalized["status"] == "SOME FUTURE STATUS"  # raw text preserved regardless


# 12. NDR normalization
def test_ndr_normalization_maps_core_fields() -> None:
    raw = {
        "id": 42,
        "awb": "AWB12345",
        "order_id": 9001,
        "reason": "Customer not available",
        "attempts": 2,
        "courier_name": "Xpressbees",
        "created_at": "2026-01-04 10:00:00",
    }
    data = NDR_NORMALIZER.normalize(raw)

    assert data["source_system"] == "shiprocket"
    assert data["external_id"] == "42"
    assert data["awb"] == "AWB12345"
    assert data["shiprocket_order_id"] == "9001"
    assert data["reason"] == "Customer not available"
    assert data["attempt_number"] == 2
    assert data["raw_external_payload"] == raw


def test_ndr_normalization_defaults_attempt_number_to_one() -> None:
    data = NDR_NORMALIZER.normalize({"id": 1, "awb": "AWB1"})
    assert data["attempt_number"] == 1


# Exact shape captured from a LIVE `GET /ndr/all` response (2026-08-28) —
# `awb_code`/`courier` (not `awb`/`courier_name`) is the real field
# naming, unlike the previously-guessed, unverified shape the two tests
# above still exercise (kept passing via a fallback, not because that
# shape is what Shiprocket actually sends).
_LIVE_NDR_RESPONSE = {
    "id": 1540207132,
    "shipment_id": 1536426985,
    "channel_order_id": "AWL91535",
    "customer_name": "Vijay Kamble",
    "reason": "Customer Not Available",
    "attempts": 1,
    "ndr_raised_at": "2026-08-28 15:19:49",
    "courier": "Bluedart Surface - Select 500gm",
    "awb_code": "77931116852",
}


def test_ndr_normalization_maps_the_live_response_shape() -> None:
    data = NDR_NORMALIZER.normalize(_LIVE_NDR_RESPONSE)

    assert data["source_system"] == "shiprocket"
    assert data["external_id"] == "1540207132"
    assert data["awb"] == "77931116852"
    assert data["reason"] == "Customer Not Available"
    assert data["external_reason"] == "Customer Not Available"
    assert data["attempt_number"] == 1
    assert data["courier_name"] == "Bluedart Surface - Select 500gm"
    assert data["external_created_at"] == datetime(2026, 8, 28, 15, 19, 49)
    assert data["raw_external_payload"] == _LIVE_NDR_RESPONSE


# Shipment normalization (pull) — added to fix the "No OMS shipment
# found for Shiprocket NDR" production incident: 102/102 real NDR
# records couldn't be linked because nothing had ever imported
# Shiprocket's existing shipments into the OMS. `channel_order_id` is
# confirmed against a live NDR response and carries the identical
# meaning here (the merchant's own order number); `awb`/`status` are
# Shiprocket's commonly documented `/shipments` field names, not yet
# independently re-verified against a live account for this specific
# endpoint.
def test_shipment_normalization_maps_core_fields() -> None:
    raw = {
        "id": 555,
        "channel_order_id": "AWL91535",
        "awb": "77931116852",
        "status": "In Transit",
    }
    data = SHIPMENT_NORMALIZER.normalize(raw)

    assert data["source_system"] == "shiprocket"
    assert data["external_id"] == "555"
    assert data["shiprocket_shipment_id"] == "555"
    assert data["channel_order_id"] == "AWL91535"
    assert data["awb"] == "77931116852"
    assert data["current_status"] == ShipmentStatus.IN_TRANSIT
    assert data["raw_external_payload"] == raw


def test_shipment_normalization_falls_back_to_shipment_id_and_awb_code() -> None:
    """A second, differently-shaped Shiprocket response variant (e.g. a
    different API version) using `shipment_id`/`awb_code` instead of
    `id`/`awb` — the same defensive-fallback pattern already used for
    NDR — must still normalize correctly.
    """
    data = SHIPMENT_NORMALIZER.normalize(
        {"shipment_id": 777, "channel_order_id": "AWL91600", "awb_code": "99988877766"}
    )

    assert data["external_id"] == "777"
    assert data["awb"] == "99988877766"


def test_shipment_normalization_with_no_channel_order_id_leaves_it_none() -> None:
    """The upsert handler treats a missing `channel_order_id` as an
    unmatchable shipment (spec: never invent an OMS order id) — the
    normalizer's only job is to pass that absence through honestly.
    """
    data = SHIPMENT_NORMALIZER.normalize({"id": 1, "awb": "AWB1"})
    assert data["channel_order_id"] is None


def test_shipment_normalization_with_unknown_status_does_not_guess() -> None:
    data = SHIPMENT_NORMALIZER.normalize({"id": 1, "awb": "AWB1", "status": "some-new-status"})
    assert data["current_status"] is None


# Round 15 fix, real production bug: GET /shipments' `created_at` uses an
# ordinal day suffix ("18th Dec 2025 03:52 PM") -- confirmed live to
# differ from GET /orders/show/{id}'s `created_at`, which does not ("21
# Dec 2025 12:49 PM"). `strptime` can't parse the ordinal form at all, so
# every real `/shipments` record silently failed to produce a
# `shiprocket_created_at`, which meant `entity_sync._upsert_shipment`'s
# incremental performance boundary never actually skipped anything --
# confirmed live via production logs still showing `skip_reason=None` and
# a live /orders/show call for every already-known-unmatchable historical
# record, including one that then hit Shiprocket's rate limit.
def test_shipment_normalization_parses_created_at_with_an_ordinal_day_suffix() -> None:
    data = SHIPMENT_NORMALIZER.normalize(
        {"id": 1, "awb": "AWB1", "created_at": "18th Dec 2025 03:52 PM"}
    )
    assert data["shiprocket_created_at"] == datetime(2025, 12, 18, 15, 52)


def test_shipment_normalization_still_parses_created_at_without_an_ordinal_suffix() -> None:
    data = SHIPMENT_NORMALIZER.normalize(
        {"id": 1, "awb": "AWB1", "created_at": "2026-01-01 09:00:00"}
    )
    assert data["shiprocket_created_at"] == datetime(2026, 1, 1, 9, 0, 0)


# 9. Courier mapping (payment-method + generic value mapping pattern)
@pytest.mark.parametrize(
    ("payment_type", "expected"),
    [
        (PaymentType.COD, "COD"),
        (PaymentType.PREPAID, "Prepaid"),
        (PaymentType.OTHER, "Prepaid"),
    ],
)
def test_payment_method_mapping_is_documented(payment_type, expected) -> None:
    assert normalize_payment_method(payment_type) == expected


# 5. Order payload mapping (OMS -> Shiprocket push)
def _fake_order() -> MagicMock:
    order = MagicMock()
    order.order_number = "OMS-1001"
    order.order_datetime = datetime(2026, 1, 1, 10, 30)
    order.shipping_address = {
        "line1": "123 MG Road",
        "line2": None,
        "city": "Mumbai",
        "state": "Maharashtra",
        "country": "India",
        "pin_code": "400001",
        "contact_name": "Jane Doe",
        "contact_phone": "9876543210",
    }
    order.customer = MagicMock(
        first_name="Jane", last_name="Doe", email="jane@example.com", phone="9876543210"
    )
    order.payment_type = PaymentType.COD
    order.subtotal = Decimal("999.00")
    item = MagicMock(
        product_name="Ashwagandha 60ct", sku="ASH-60", quantity=2, unit_price=Decimal("499.50")
    )
    order.items = [item]
    return order


def test_order_push_payload_maps_billing_and_items() -> None:
    order = _fake_order()
    payload = ShiprocketOrderPushNormalizer().build_payload(order, pickup_location="Main Warehouse")

    assert payload["order_id"] == "OMS-1001"
    assert payload["pickup_location"] == "Main Warehouse"
    assert payload["billing_customer_name"] == "Jane"
    assert payload["billing_last_name"] == "Doe"
    assert payload["billing_address"] == "123 MG Road"
    assert payload["billing_pincode"] == "400001"
    assert payload["billing_phone"] == "9876543210"
    assert payload["payment_method"] == "COD"
    assert payload["sub_total"] == "999.00"
    assert payload["order_items"] == [
        {"name": "Ashwagandha 60ct", "sku": "ASH-60", "units": 2, "selling_price": "499.50"}
    ]


def test_order_push_payload_dimension_overrides() -> None:
    order = _fake_order()
    payload = ShiprocketOrderPushNormalizer().build_payload(
        order,
        pickup_location="Main Warehouse",
        length_cm=20,
        breadth_cm=15,
        height_cm=5,
        weight_kg=1.2,
    )
    assert payload["length"] == 20
    assert payload["breadth"] == 15
    assert payload["height"] == 5
    assert payload["weight"] == 1.2


def test_order_push_payload_falls_back_to_customer_name_without_address_contact() -> None:
    order = _fake_order()
    order.shipping_address = {
        "line1": "1 St",
        "city": "Pune",
        "pin_code": "411001",
        "country": "India",
    }
    payload = ShiprocketOrderPushNormalizer().build_payload(order, pickup_location="Main Warehouse")
    assert payload["billing_customer_name"] == "Jane"
    assert payload["billing_last_name"] == "Doe"
