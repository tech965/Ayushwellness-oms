"""Shopify -> OMS field mapping. Pure functions — no DB, no network."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.integrations.shopify.normalizer import (
    ShopifyCustomerNormalizer,
    ShopifyOrderNormalizer,
    ShopifyProductNormalizer,
    normalize_fulfillment_status,
    normalize_payment_status,
    normalize_payment_type,
    normalize_shipment_status,
    normalize_tags,
)
from app.integrations.shopify.webhook_shapes import order_webhook_to_graphql_shape
from app.models.enums import FulfillmentStatus, PaymentStatus, PaymentType


# 4. Customer normalization
def test_customer_normalization_maps_core_fields_and_addresses() -> None:
    raw = {
        "id": "gid://shopify/Customer/123",
        "firstName": "Jane",
        "lastName": "Doe",
        "email": "jane@example.com",
        "phone": "+911234567890",
        "state": "ENABLED",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
        "defaultAddress": {
            "id": "gid://shopify/MailingAddress/1",
            "address1": "123 MG Road",
            "city": "Mumbai",
            "province": "Maharashtra",
            "country": "India",
            "zip": "400001",
            "phone": "+911234567890",
            "name": "Jane Doe",
        },
        "addresses": [],
    }

    data = ShopifyCustomerNormalizer().normalize(raw)

    assert data["source_system"] == "shopify"
    assert data["external_id"] == "123"
    assert data["shopify_customer_id"] == "123"
    assert data["full_name"] == "Jane Doe"
    assert data["email"] == "jane@example.com"
    assert data["is_active"] is True
    assert len(data["addresses"]) == 1
    assert data["addresses"][0]["pin_code"] == "400001"
    assert data["addresses"][0]["is_default"] is True
    assert data["raw_external_payload"] == raw


def test_customer_normalization_disabled_state_maps_to_inactive() -> None:
    raw = {"id": "gid://shopify/Customer/1", "state": "DISABLED"}
    data = ShopifyCustomerNormalizer().normalize(raw)
    assert data["is_active"] is False


# 7. Product normalization
def test_product_normalization_maps_type_and_tags() -> None:
    raw = {
        "id": "gid://shopify/Product/55",
        "title": "Ashwagandha Capsules",
        "vendor": "AyushWellness",
        "productType": "Supplement",
        "status": "ACTIVE",
        "tags": ["herbal", "immunity"],
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
        "variants": {"edges": []},
    }

    data = ShopifyProductNormalizer().normalize(raw)

    assert data["external_id"] == "55"
    assert data["product_type"] == "Supplement"
    assert data["tags"] == "herbal, immunity"
    assert data["status"].value == "active"


# 8. Product variant normalization
def test_variant_normalization_maps_price_barcode_and_options() -> None:
    raw = {
        "id": "gid://shopify/Product/55",
        "title": "Ashwagandha",
        "variants": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductVariant/99",
                        "sku": "ASH-60",
                        "title": "60 count",
                        "price": "499.00",
                        "compareAtPrice": "599.00",
                        "inventoryQuantity": 120,
                        "weight": "0.2",
                        "barcode": "8901234567890",
                        "selectedOptions": [{"name": "Size", "value": "60ct"}],
                    }
                }
            ]
        },
    }

    data = ShopifyProductNormalizer().normalize(raw)
    variant = data["variants"][0]

    assert variant["external_id"] == "99"
    assert variant["sku"] == "ASH-60"
    assert variant["price"] == Decimal("499.00")
    assert variant["compare_at_price"] == Decimal("599.00")
    assert variant["barcode"] == "8901234567890"
    assert variant["options"] == {"Size": "60ct"}


def test_variant_without_sku_falls_back_to_a_deterministic_sku() -> None:
    raw = {
        "id": "gid://shopify/Product/1",
        "variants": {"edges": [{"node": {"id": "gid://shopify/ProductVariant/1", "sku": None}}]},
    }
    data = ShopifyProductNormalizer().normalize(raw)
    assert data["variants"][0]["sku"] == "shopify-1"


# 11. Order normalization / 12. Order item normalization
def test_order_normalization_maps_totals_addresses_and_line_items() -> None:
    raw = {
        "id": "gid://shopify/Order/777",
        "name": "#1001",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
        "cancelledAt": None,
        "currencyCode": "INR",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED",
        "subtotalPriceSet": {"shopMoney": {"amount": "1000.00"}},
        "totalDiscountsSet": {"shopMoney": {"amount": "50.00"}},
        "totalTaxSet": {"shopMoney": {"amount": "90.00"}},
        "totalPriceSet": {"shopMoney": {"amount": "1089.00"}},
        "shippingLine": {"originalPriceSet": {"shopMoney": {"amount": "49.00"}}},
        "paymentGatewayNames": ["Razorpay"],
        "customer": {"id": "gid://shopify/Customer/123"},
        "shippingAddress": {
            "address1": "123 MG Road",
            "city": "Mumbai",
            "province": "Maharashtra",
            "country": "India",
            "zip": "400001",
            "phone": None,
            "name": "Jane Doe",
        },
        "billingAddress": None,
        "lineItems": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/LineItem/1",
                        "sku": "ASH-60",
                        "title": "Ashwagandha 60ct",
                        "quantity": 2,
                        "originalUnitPriceSet": {"shopMoney": {"amount": "500.00"}},
                        "discountedTotalSet": {"shopMoney": {"amount": "950.00"}},
                        "variant": {"id": "gid://shopify/ProductVariant/99"},
                    }
                }
            ]
        },
    }

    data = ShopifyOrderNormalizer().normalize(raw)

    assert data["external_id"] == "777"
    assert data["order_number"] == "#1001"
    assert data["customer_external_id"] == "123"
    assert data["subtotal"] == Decimal("1000.00")
    assert data["total_amount"] == Decimal("1089.00")
    assert data["shipping_charge"] == Decimal("49.00")
    assert data["payment_status"] == PaymentStatus.PAID
    assert data["fulfillment_status"] == FulfillmentStatus.UNFULFILLED
    assert data["payment_type"] == PaymentType.PREPAID
    assert data["is_cancelled"] is False
    assert data["shipping_address"]["pin_code"] == "400001"
    assert data["billing_address"] is None

    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["external_id"] == "1"
    assert item["sku"] == "ASH-60"
    assert item["quantity"] == 2
    assert item["shopify_variant_id"] == "99"
    assert item["unit_price"] == Decimal("500.00")
    # `discountedTotalSet` (950.00) is the line's *after-discount total*,
    # not a discount amount — total_amount must equal it directly, and
    # discount_amount is derived as unit_price*qty - discountedTotalSet
    # (1000.00 - 950.00 = 50.00). Regression test for the bug where this
    # used to be swapped, zeroing total_amount whenever a line had no
    # per-line discount.
    assert item["total_amount"] == Decimal("950.00")
    assert item["discount_amount"] == Decimal("50.00")


# Issue 4: Shopify order tags/note import.


def _minimal_order_raw(**overrides) -> dict:
    base = {
        "id": "gid://shopify/Order/1",
        "name": "#1",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "cancelledAt": None,
        "currencyCode": "INR",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED",
        "subtotalPriceSet": {"shopMoney": {"amount": "100.00"}},
        "totalDiscountsSet": {"shopMoney": {"amount": "0.00"}},
        "totalTaxSet": {"shopMoney": {"amount": "0.00"}},
        "totalPriceSet": {"shopMoney": {"amount": "100.00"}},
        "shippingLine": None,
        "paymentGatewayNames": ["Razorpay"],
        "customer": None,
        "shippingAddress": None,
        "billingAddress": None,
        "lineItems": {"edges": []},
    }
    base.update(overrides)
    return base


def test_order_normalization_maps_tags_and_note() -> None:
    raw = _minimal_order_raw(
        tags=["COD", "VIP", "Repeat Customer"], note="Please deliver after 6 PM"
    )
    data = ShopifyOrderNormalizer().normalize(raw)
    assert data["shopify_tags"] == ["COD", "VIP", "Repeat Customer"]
    assert data["shopify_order_note"] == "Please deliver after 6 PM"


def test_order_normalization_handles_missing_tags_and_note_without_crashing() -> None:
    """Neither key present on the raw payload (e.g. an older cached
    response, or a field Shopify omitted) -- must degrade to an empty
    list/None, matching every other field's `.get()`-defensive handling,
    never crash the sync.
    """
    raw = _minimal_order_raw()
    assert "tags" not in raw and "note" not in raw
    data = ShopifyOrderNormalizer().normalize(raw)
    assert data["shopify_tags"] == []
    assert data["shopify_order_note"] is None


def test_order_normalization_handles_explicit_null_tags_and_note() -> None:
    raw = _minimal_order_raw(tags=None, note=None)
    data = ShopifyOrderNormalizer().normalize(raw)
    assert data["shopify_tags"] == []
    assert data["shopify_order_note"] is None


def test_normalize_tags_accepts_a_graphql_list() -> None:
    assert normalize_tags(["COD", "VIP"]) == ["COD", "VIP"]


def test_normalize_tags_accepts_a_rest_comma_joined_string() -> None:
    assert normalize_tags("COD, VIP, Repeat Customer") == ["COD", "VIP", "Repeat Customer"]


def test_normalize_tags_drops_blank_entries() -> None:
    assert normalize_tags("COD, , VIP,") == ["COD", "VIP"]


def test_normalize_tags_of_none_is_an_empty_list() -> None:
    assert normalize_tags(None) == []


def test_order_webhook_shape_translates_rest_tags_and_note_for_the_normalizer() -> None:
    """REST webhook delivery shape (`orders/updated`) -> the same
    normalizer the GraphQL pull-sync path uses, unmodified.
    """
    rest_payload = {
        "id": 900,
        "name": "#900",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "tags": "COD, VIP",
        "note": "Call before delivery",
        "line_items": [],
    }
    graphql_shape = order_webhook_to_graphql_shape(rest_payload)
    data = ShopifyOrderNormalizer().normalize(graphql_shape)
    assert data["shopify_tags"] == ["COD", "VIP"]
    assert data["shopify_order_note"] == "Call before delivery"


# Shipment Status regression: `Fulfillment.displayStatus` (the real
# Shopify delivery/shipment-progress status) must never be conflated with
# `Order.displayFulfillmentStatus` (fulfillment_status).


def test_order_normalization_maps_shipment_status_from_fulfillment_display_status() -> None:
    raw = _minimal_order_raw(fulfillments=[{"displayStatus": "IN_TRANSIT"}])
    data = ShopifyOrderNormalizer().normalize(raw)
    assert data["shopify_shipment_status"] == "in_transit"
    # Never conflated with the unrelated, separately-mapped field.
    assert data["fulfillment_status"] != data["shopify_shipment_status"]


def test_order_normalization_with_no_fulfillments_yet_leaves_shipment_status_none() -> None:
    """No fabricated default -- an order with zero fulfillments genuinely
    has no delivery status yet.
    """
    raw = _minimal_order_raw(fulfillments=[])
    data = ShopifyOrderNormalizer().normalize(raw)
    assert data["shopify_shipment_status"] is None


def test_order_normalization_missing_fulfillments_key_leaves_shipment_status_none() -> None:
    raw = _minimal_order_raw()
    assert "fulfillments" not in raw
    data = ShopifyOrderNormalizer().normalize(raw)
    assert data["shopify_shipment_status"] is None


def test_normalize_shipment_status_takes_the_last_fulfillments_non_null_status() -> None:
    """An order can have more than one Fulfillment (split shipments) --
    the LAST one is treated as "current", matching `_to_list_response`'s
    existing `order.shipments[-1]` convention for Shiprocket shipments.
    """
    assert (
        normalize_shipment_status(
            [{"displayStatus": "DELIVERED"}, {"displayStatus": "OUT_FOR_DELIVERY"}]
        )
        == "out_for_delivery"
    )


def test_normalize_shipment_status_skips_trailing_null_and_uses_the_last_real_value() -> None:
    """A fulfillment can exist with `displayStatus` still null (right
    after creation, before Shopify's tracking pipeline has an update) --
    must fall back to the nearest real status, not report `None` while an
    earlier fulfillment clearly does have one.
    """
    assert (
        normalize_shipment_status([{"displayStatus": "DELIVERED"}, {"displayStatus": None}])
        == "delivered"
    )


def test_normalize_shipment_status_of_empty_list_is_none() -> None:
    assert normalize_shipment_status([]) is None


def test_normalize_shipment_status_of_non_list_is_none() -> None:
    assert normalize_shipment_status(None) is None


def test_order_webhook_shape_translates_rest_fulfillment_shipment_status() -> None:
    """REST webhook delivery shape -- `Fulfillment.shipment_status`
    ("in_transit"/"delivered"/...) -> the same normalizer the GraphQL
    pull-sync path uses, unmodified.
    """
    rest_payload = {
        "id": 901,
        "name": "#901",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "line_items": [],
        "fulfillments": [{"shipment_status": "in_transit"}],
    }
    graphql_shape = order_webhook_to_graphql_shape(rest_payload)
    data = ShopifyOrderNormalizer().normalize(graphql_shape)
    assert data["shopify_shipment_status"] == "in_transit"


def test_order_webhook_shape_handles_a_fulfillment_with_no_shipment_status_yet() -> None:
    rest_payload = {
        "id": 902,
        "name": "#902",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "line_items": [],
        "fulfillments": [{"shipment_status": None}],
    }
    graphql_shape = order_webhook_to_graphql_shape(rest_payload)
    data = ShopifyOrderNormalizer().normalize(graphql_shape)
    assert data["shopify_shipment_status"] is None


def test_line_item_with_no_discount_still_gets_a_nonzero_total() -> None:
    """The exact real-world shape of the reported bug: a line with no
    per-line discount used to compute total_amount = unit_price*qty -
    discountedTotalSet ~= 0, instead of total_amount = discountedTotalSet.
    """
    raw = {
        "id": "gid://shopify/LineItem/2",
        "sku": "ASH-30",
        "title": "Ashwagandha 30ct",
        "quantity": 1,
        "originalUnitPriceSet": {"shopMoney": {"amount": "649.00"}},
        "discountedTotalSet": {"shopMoney": {"amount": "649.00"}},
        "variant": {"id": "gid://shopify/ProductVariant/1"},
    }

    item = ShopifyOrderNormalizer._normalize_line_item(raw)

    assert item["total_amount"] == Decimal("649.00")
    assert item["discount_amount"] == Decimal("0")


def test_order_with_cancelled_at_is_flagged_cancelled() -> None:
    raw = {"id": "gid://shopify/Order/1", "cancelledAt": "2026-01-05T00:00:00Z", "lineItems": {}}
    data = ShopifyOrderNormalizer().normalize(raw)
    assert data["is_cancelled"] is True


# 15. Payment mapping — the documented Shopify -> OMS status/type tables
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("PENDING", PaymentStatus.PENDING),
        ("AUTHORIZED", PaymentStatus.AUTHORIZED),
        ("PARTIALLY_PAID", PaymentStatus.PAID),
        ("PAID", PaymentStatus.PAID),
        ("PARTIALLY_REFUNDED", PaymentStatus.PARTIALLY_REFUNDED),
        ("REFUNDED", PaymentStatus.REFUNDED),
        ("VOIDED", PaymentStatus.FAILED),
        ("EXPIRED", PaymentStatus.FAILED),
        ("SOME_UNKNOWN_FUTURE_VALUE", PaymentStatus.PENDING),
        (None, PaymentStatus.PENDING),
    ],
)
def test_payment_status_mapping_is_documented_and_total(raw_status, expected) -> None:
    assert normalize_payment_status(raw_status) == expected


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("UNFULFILLED", FulfillmentStatus.UNFULFILLED),
        ("PARTIALLY_FULFILLED", FulfillmentStatus.PARTIAL),
        ("FULFILLED", FulfillmentStatus.FULFILLED),
        ("ON_HOLD", FulfillmentStatus.PARTIAL),
        (None, FulfillmentStatus.UNFULFILLED),
    ],
)
def test_fulfillment_status_mapping_is_documented_and_total(raw_status, expected) -> None:
    assert normalize_fulfillment_status(raw_status) == expected


def test_payment_type_detects_cod_gateway_case_insensitively() -> None:
    assert normalize_payment_type(["Cash on Delivery (COD)"]) == PaymentType.COD
    assert normalize_payment_type(["cash on delivery"]) == PaymentType.COD


def test_payment_type_defaults_to_prepaid_for_a_known_online_gateway() -> None:
    assert normalize_payment_type(["Razorpay"]) == PaymentType.PREPAID


def test_payment_type_with_no_gateway_is_other() -> None:
    assert normalize_payment_type([]) == PaymentType.OTHER
    assert normalize_payment_type(None) == PaymentType.OTHER


# Round 4 — 3 real, confirmed sync-error root causes found by live-
# verifying the customers/products backstop against the actual store.


def test_customer_normalization_strips_null_bytes_instead_of_crashing_the_insert() -> None:
    """Real error reproduced: `UntranslatableCharacterError` — Postgres
    text columns reject a raw NUL byte outright.
    """
    raw = {
        "id": "gid://shopify/Customer/7459254665405",
        "firstName": "Asif\x00",
        "lastName": "S\x00ayyed",
        "email": "asif\x00@example.com",
        "phone": None,
        "state": "ENABLED",
    }
    normalized = ShopifyCustomerNormalizer().normalize(raw)
    assert "\x00" not in normalized["first_name"]
    assert "\x00" not in normalized["last_name"]
    assert "\x00" not in normalized["full_name"]
    assert "\x00" not in normalized["email"]


def test_address_normalization_truncates_an_oversized_field_instead_of_crashing() -> None:
    """Real error reproduced: `StringDataRightTruncationError` on
    `customer_addresses.contact_phone`, which is `VARCHAR(32)`.
    """
    from app.integrations.shopify.normalizer import normalize_address

    too_long_phone = "+91-9876543210 / +91-9876500000 (alt)"  # 38 chars
    assert len(too_long_phone) > 32

    address = normalize_address({"phone": too_long_phone, "address1": "1 MG Road", "city": "Pune"})
    assert address is not None
    assert len(address["contact_phone"]) <= 32
    assert address["contact_phone"] == too_long_phone[:32]


def test_address_normalization_strips_null_bytes_from_every_text_field() -> None:
    from app.integrations.shopify.normalizer import normalize_address

    address = normalize_address(
        {"address1": "Gala\x00 5", "city": "Thane\x00", "phone": "+9175\x0007276437"}
    )
    assert address is not None
    assert "\x00" not in address["line1"]
    assert "\x00" not in address["city"]
    assert "\x00" not in address["contact_phone"]


# Round 14 — real production evidence: the same `UntranslatableCharacterError`
# from Round 4 recurred for the *same* customer external_id (7459254665405)
# even though `first_name`/`last_name`/`email` were all confirmed clean in
# the crashing INSERT's own logged parameters. Root cause: `_clean_text` was
# only ever applied to individually-extracted fields — `raw_external_payload`
# was always passed straight through unsanitized, and Postgres's `jsonb`
# rejects a raw NUL byte in a string value exactly like `text`/`varchar`
# does. These prove the *blob*, not just the extracted fields, is now clean.


def test_customer_normalization_strips_null_bytes_from_raw_external_payload() -> None:
    raw = {
        "id": "gid://shopify/Customer/7459254665405",
        "firstName": "Amit",
        "lastName": ".",
        "email": "amitm16@icloud.com",
        "state": "ENABLED",
        "note": "VIP customer\x00 - handle with care",
    }
    normalized = ShopifyCustomerNormalizer().normalize(raw)
    assert "\x00" not in normalized["raw_external_payload"]["note"]
    # structure/other values must survive unchanged
    assert normalized["raw_external_payload"]["firstName"] == "Amit"
    assert normalized["raw_external_payload"]["id"] == "gid://shopify/Customer/7459254665405"


def test_order_normalization_strips_null_bytes_from_raw_external_payload() -> None:
    raw = {
        "id": "gid://shopify/Order/6676426948797",
        "name": "#AWL91535",
        "createdAt": "2026-01-01T00:00:00Z",
        "customer": {"id": "gid://shopify/Customer/123"},
        "shippingAddress": None,
        "billingAddress": None,
        "lineItems": {"edges": []},
        "note": "Deliver after 5pm\x00",
        "customAttributes": [{"key": "gift_message", "value": "Happy\x00 Birthday"}],
    }
    normalized = ShopifyOrderNormalizer().normalize(raw)
    payload = normalized["raw_external_payload"]
    assert "\x00" not in payload["note"]
    assert "\x00" not in payload["customAttributes"][0]["value"]
    assert payload["customAttributes"][0]["key"] == "gift_message"
    assert payload["name"] == "#AWL91535"


def test_product_normalization_strips_null_bytes_from_raw_external_payload() -> None:
    raw = {
        "id": "gid://shopify/Product/55",
        "title": "Ashwagandha Capsules",
        "status": "ACTIVE",
        "descriptionHtml": "<p>Pure\x00 herbal extract</p>",
        "variants": {"edges": []},
    }
    normalized = ShopifyProductNormalizer().normalize(raw)
    assert "\x00" not in normalized["raw_external_payload"]["descriptionHtml"]


def test_variant_normalization_strips_null_bytes_from_raw_external_payload() -> None:
    raw = {
        "id": "gid://shopify/Product/55",
        "title": "Ashwagandha",
        "variants": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductVariant/99",
                        "sku": "ASH-60",
                        "title": "60 count\x00",
                        "price": "499.00",
                    }
                }
            ]
        },
    }
    data = ShopifyProductNormalizer().normalize(raw)
    variant_payload = data["variants"][0]["raw_external_payload"]
    assert "\x00" not in variant_payload["title"]
