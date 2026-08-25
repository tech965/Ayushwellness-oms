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
)
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
