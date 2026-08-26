"""Maps raw Shopify GraphQL node dicts to the kwargs the OMS
Customer/Product/Order services expect. Nothing outside this module
should ever branch on a Shopify-specific string — see
`docs/architecture/integrations.md#status-normalization`.

Every mapping table below is exhaustive over the enum values documented
for the 2026-01 GraphQL Admin API; an unrecognized raw value falls back
to a safe default rather than raising, since Shopify's schema is not
introspectable here without live credentials.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.integrations.normalizer import (
    CustomerNormalizer,
    Normalizer,
    OrderNormalizer,
    ProductNormalizer,
)
from app.models.enums import FulfillmentStatus, PaymentStatus, PaymentType, ProductStatus
from app.models.mixins import SourceSystem


def _gid_to_external_id(gid: str | None) -> str | None:
    """`"gid://shopify/Customer/123456"` -> `"123456"`. Falls back to the
    raw value unchanged if it isn't a GID (defensive against REST-shaped
    ids or a schema change), so a sync never crashes over id formatting.
    """
    if not gid:
        return None
    return gid.rsplit("/", 1)[-1] if "/" in gid else gid


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(default)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _money(money_set: dict | None) -> Decimal:
    if not money_set:
        return Decimal("0")
    shop_money = money_set.get("shopMoney") or {}
    return _decimal(shop_money.get("amount"))


# --- Address --------------------------------------------------------------


def normalize_address(raw: dict | None, *, is_default: bool = False) -> dict[str, Any] | None:
    if not raw:
        return None
    name = raw.get("name") or ""
    address: dict[str, Any] = {
        "line1": raw.get("address1") or "—",
        "line2": raw.get("address2"),
        "city": raw.get("city") or "—",
        "state": raw.get("province"),
        "country": raw.get("country") or "India",
        "pin_code": raw.get("zip") or "",
        "contact_name": name or None,
        "contact_phone": raw.get("phone"),
        "is_default": is_default,
    }
    # Only `Customer.addresses`/`defaultAddress` carry a stable Shopify id
    # (`MailingAddress.id`) — `Order.shippingAddress`/`billingAddress` are
    # point-in-time snapshots with no id to key an upsert on.
    external_id = _gid_to_external_id(raw.get("id")) if raw.get("id") else None
    if external_id:
        address["external_id"] = external_id
    return address


# --- Customer ---------------------------------------------------------


class ShopifyCustomerNormalizer(CustomerNormalizer):
    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        first_name = raw.get("firstName")
        last_name = raw.get("lastName")
        full_name = " ".join(filter(None, [first_name, last_name])) or None

        default_raw = raw.get("defaultAddress") or {}
        addresses = [
            normalize_address(a, is_default=(a.get("id") == default_raw.get("id")))
            for a in (raw.get("addresses") or [])
        ]
        seen_external_ids = {a.get("external_id") for a in addresses if a and a.get("external_id")}
        default_address = normalize_address(default_raw, is_default=True) if default_raw else None
        if default_address and default_address.get("external_id") not in seen_external_ids:
            addresses.append(default_address)

        return {
            "source_system": SourceSystem.SHOPIFY,
            "external_id": _gid_to_external_id(raw.get("id")),
            "shopify_customer_id": _gid_to_external_id(raw.get("id")),
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "email": raw.get("email"),
            "phone": raw.get("phone"),
            "is_active": raw.get("state") != "DISABLED",
            "external_created_at": _parse_datetime(raw.get("createdAt")),
            "external_updated_at": _parse_datetime(raw.get("updatedAt")),
            "raw_external_payload": raw,
            "addresses": [a for a in addresses if a is not None],
        }


# --- Product ------------------------------------------------------------

# Shopify ProductStatus (GraphQL enum) -> OMS ProductStatus
_PRODUCT_STATUS_MAP: dict[str, ProductStatus] = {
    "ACTIVE": ProductStatus.ACTIVE,
    "DRAFT": ProductStatus.DRAFT,
    "ARCHIVED": ProductStatus.ARCHIVED,
}


def normalize_product_status(raw_status: str | None) -> ProductStatus:
    return _PRODUCT_STATUS_MAP.get((raw_status or "").upper(), ProductStatus.DRAFT)


class ShopifyProductNormalizer(ProductNormalizer):
    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        variants = [
            self._normalize_variant(edge["node"])
            for edge in (raw.get("variants", {}).get("edges") or [])
        ]
        tags = raw.get("tags")

        return {
            "source_system": SourceSystem.SHOPIFY,
            "external_id": _gid_to_external_id(raw.get("id")),
            "shopify_product_id": _gid_to_external_id(raw.get("id")),
            "title": raw.get("title") or "Untitled product",
            "description": raw.get("descriptionHtml"),
            "vendor": raw.get("vendor"),
            "product_type": raw.get("productType"),
            "tags": ", ".join(tags) if isinstance(tags, list) else tags,
            "status": normalize_product_status(raw.get("status")),
            "external_created_at": _parse_datetime(raw.get("createdAt")),
            "external_updated_at": _parse_datetime(raw.get("updatedAt")),
            "raw_external_payload": raw,
            "variants": variants,
        }

    @staticmethod
    def _normalize_variant(raw: dict[str, Any]) -> dict[str, Any]:
        options = {
            opt["name"]: opt["value"]
            for opt in (raw.get("selectedOptions") or [])
            if opt.get("name")
        }
        return {
            "source_system": SourceSystem.SHOPIFY,
            "external_id": _gid_to_external_id(raw.get("id")),
            "shopify_variant_id": _gid_to_external_id(raw.get("id")),
            "sku": raw.get("sku") or f"shopify-{_gid_to_external_id(raw.get('id'))}",
            "title": raw.get("title"),
            "price": _decimal(raw.get("price")),
            "compare_at_price": (
                _decimal(raw["compareAtPrice"]) if raw.get("compareAtPrice") else None
            ),
            "inventory_quantity": raw.get("inventoryQuantity") or 0,
            # `ProductVariant.weight` no longer exists on Shopify's live
            # GraphQL schema (confirmed against a real store — it's been
            # removed in favor of `inventoryItem.measurement.weight`) —
            # `queries.py` no longer requests it, so this is always None
            # until PRODUCTS_QUERY is updated to fetch weight via that
            # nested path instead.
            "weight": _decimal(raw.get("weight")) if raw.get("weight") is not None else None,
            "barcode": raw.get("barcode"),
            "options": options or None,
            "status": ProductStatus.ACTIVE,
            "raw_external_payload": raw,
        }


# --- Order ----------------------------------------------------------------

# Shopify displayFinancialStatus -> OMS PaymentStatus. Shopify has no
# concept between "some money captured" and "fully captured" beyond
# PARTIALLY_PAID; the OMS enum has no partial-payment state either, so
# PARTIALLY_PAID maps to PAID (order is being treated as paid
# operationally) — the raw Shopify string survives in
# `raw_external_payload` for exact reconciliation.
_PAYMENT_STATUS_MAP: dict[str, PaymentStatus] = {
    "PENDING": PaymentStatus.PENDING,
    "AUTHORIZED": PaymentStatus.AUTHORIZED,
    "PARTIALLY_PAID": PaymentStatus.PAID,
    "PAID": PaymentStatus.PAID,
    "PARTIALLY_REFUNDED": PaymentStatus.PARTIALLY_REFUNDED,
    "REFUNDED": PaymentStatus.REFUNDED,
    "VOIDED": PaymentStatus.FAILED,
    "EXPIRED": PaymentStatus.FAILED,
}

# Shopify displayFulfillmentStatus -> OMS FulfillmentStatus. Every
# Shopify value that isn't a clean "nothing shipped yet"/"fully shipped"
# (ON_HOLD, SCHEDULED, IN_PROGRESS, PARTIALLY_FULFILLED, ...) maps to
# PARTIAL — an accurate enough operational signal ("this order needs
# attention") without inventing OMS enum values Shopify doesn't have an
# exact match for.
_FULFILLMENT_STATUS_MAP: dict[str, FulfillmentStatus] = {
    "UNFULFILLED": FulfillmentStatus.UNFULFILLED,
    "FULFILLED": FulfillmentStatus.FULFILLED,
    "PARTIALLY_FULFILLED": FulfillmentStatus.PARTIAL,
    "RESTOCKED": FulfillmentStatus.UNFULFILLED,
    "PENDING_FULFILLMENT": FulfillmentStatus.UNFULFILLED,
    "OPEN": FulfillmentStatus.PARTIAL,
    "IN_PROGRESS": FulfillmentStatus.PARTIAL,
    "ON_HOLD": FulfillmentStatus.PARTIAL,
    "SCHEDULED": FulfillmentStatus.UNFULFILLED,
}

_COD_GATEWAY_MARKERS = ("cod", "cash on delivery", "cash_on_delivery")


def normalize_payment_status(raw_status: str | None) -> PaymentStatus:
    return _PAYMENT_STATUS_MAP.get((raw_status or "").upper(), PaymentStatus.PENDING)


def normalize_fulfillment_status(raw_status: str | None) -> FulfillmentStatus:
    return _FULFILLMENT_STATUS_MAP.get((raw_status or "").upper(), FulfillmentStatus.UNFULFILLED)


def normalize_payment_type(gateway_names: list[str] | None) -> PaymentType:
    if not gateway_names:
        return PaymentType.OTHER
    joined = " ".join(gateway_names).lower()
    if any(marker in joined for marker in _COD_GATEWAY_MARKERS):
        return PaymentType.COD
    return PaymentType.PREPAID


class ShopifyOrderNormalizer(OrderNormalizer):
    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        external_id = _gid_to_external_id(raw.get("id"))
        financial_status = raw.get("displayFinancialStatus")
        customer = raw.get("customer") or {}

        line_items = [
            self._normalize_line_item(edge["node"])
            for edge in (raw.get("lineItems", {}).get("edges") or [])
        ]

        return {
            "source_system": SourceSystem.SHOPIFY,
            "external_id": external_id,
            "shopify_order_id": external_id,
            "order_number": raw.get("name") or f"#shopify-{external_id}",
            "customer_external_id": _gid_to_external_id(customer.get("id")),
            "order_datetime": _parse_datetime(raw.get("createdAt")),
            "currency": raw.get("currencyCode") or "INR",
            "subtotal": _money(raw.get("subtotalPriceSet")),
            "discount_amount": _money(raw.get("totalDiscountsSet")),
            "tax_amount": _money(raw.get("totalTaxSet")),
            "shipping_charge": _money((raw.get("shippingLine") or {}).get("originalPriceSet")),
            "total_amount": _money(raw.get("totalPriceSet")),
            "payment_status": normalize_payment_status(financial_status),
            "fulfillment_status": normalize_fulfillment_status(raw.get("displayFulfillmentStatus")),
            "payment_type": normalize_payment_type(raw.get("paymentGatewayNames")),
            "is_cancelled": bool(raw.get("cancelledAt")),
            "shipping_address": normalize_address(raw.get("shippingAddress")),
            "billing_address": normalize_address(raw.get("billingAddress")),
            "external_created_at": _parse_datetime(raw.get("createdAt")),
            "external_updated_at": _parse_datetime(raw.get("updatedAt")),
            "raw_external_payload": raw,
            "items": line_items,
        }

    @staticmethod
    def _normalize_line_item(raw: dict[str, Any]) -> dict[str, Any]:
        quantity = raw.get("quantity") or 0
        unit_price = _money(raw.get("originalUnitPriceSet"))
        # `discountedTotalSet` is the line's *after-discount total* (what
        # the customer actually paid for the line) — NOT a discount
        # amount. Treating it as a discount and subtracting it from
        # unit_price*quantity used to zero out total_amount whenever a
        # line had no per-line discount (unit_price*qty - discountedTotal
        # ~= 0). Derive discount_amount from it instead of the reverse.
        # See scripts/backfill_order_item_totals.py for the one-off fix
        # to rows already written by the old, buggy formula.
        line_total_after_discount = _money(raw.get("discountedTotalSet"))
        discount_amount = max(Decimal("0"), (unit_price * quantity) - line_total_after_discount)
        # Shopify's line-item GraphQL shape has no dedicated tax-amount
        # money field at this selection depth; tax is aggregated at the
        # order level (totalTaxSet) rather than per line item.
        tax_amount = Decimal("0")
        variant = raw.get("variant") or {}

        return {
            "external_id": _gid_to_external_id(raw.get("id")),
            "shopify_variant_id": _gid_to_external_id(variant.get("id")),
            "sku": raw.get("sku") or "",
            "product_name": raw.get("title") or "Unknown item",
            "quantity": quantity,
            "unit_price": unit_price,
            "discount_amount": discount_amount,
            "tax_amount": tax_amount,
            "total_amount": line_total_after_discount + tax_amount,
        }


CUSTOMER_NORMALIZER: Normalizer = ShopifyCustomerNormalizer()
PRODUCT_NORMALIZER: Normalizer = ShopifyProductNormalizer()
ORDER_NORMALIZER: Normalizer = ShopifyOrderNormalizer()

ENTITY_NORMALIZERS: dict[str, Normalizer] = {
    "customers": CUSTOMER_NORMALIZER,
    "products": PRODUCT_NORMALIZER,
    "orders": ORDER_NORMALIZER,
}
