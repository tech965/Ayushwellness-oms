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
    RefundNormalizer,
)
from app.models.enums import (
    FulfillmentStatus,
    PaymentStatus,
    PaymentType,
    ProductStatus,
    RefundStatus,
)
from app.models.mixins import SourceSystem


def _gid_to_external_id(gid: str | int | None) -> str | None:
    """`"gid://shopify/Customer/123456"` -> `"123456"`. Falls back to the
    raw value unchanged if it isn't a GID (defensive against REST-shaped
    ids or a schema change), so a sync never crashes over id formatting.

    Round 4 fix: the docstring above always claimed REST-shaped ids were
    handled defensively, but a REST id is a plain `int` (Shopify webhook
    payloads are REST-shaped, not GraphQL — see `webhook_shapes.py`), and
    `"/" in gid` raises `TypeError` on an int rather than falling back.
    Coercing to `str` first makes the existing docstring's claim actually
    true; behavior for the GraphQL string ids this was already handling
    is unchanged (`str` of a `str` is a no-op).
    """
    if not gid:
        return None
    gid_str = str(gid)
    return gid_str.rsplit("/", 1)[-1] if "/" in gid_str else gid_str


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(default)


def _clean_text(value: Any, *, max_len: int | None = None) -> str | None:
    """Round 4 fix: two real, confirmed failure modes from live Shopify
    customer/address data, both raised straight out of the INSERT and
    correctly (but permanently) dropped that one record every sync:

    - a raw NUL byte (`\\x00`) in a text field — Postgres `text`/`varchar`
      columns reject it outright (`UntranslatableCharacterError`), so it
      must be stripped, not merely escaped.
    - a value longer than its column (e.g. a 32-char `contact_phone`)
      raising `StringDataRightTruncationError` instead of being silently
      truncated by Postgres the way some other databases would.

    Applied at the normalizer boundary so every caller gets a value
    that's guaranteed insertable, without widening any column or
    guessing at a merchant's data-entry mistake.
    """
    if value is None:
        return None
    cleaned = str(value).replace("\x00", "")
    return cleaned[:max_len] if max_len is not None else cleaned


def _sanitize_raw_payload(value: Any) -> Any:
    """`raw_external_payload` is stored as-is by every normalizer below,
    unlike every individually-extracted field, which already goes
    through `_clean_text`. A raw NUL byte anywhere inside this blob
    crashes the INSERT the same way it does in a `text`/`varchar`
    column — Postgres's `jsonb` type rejects `\\u0000` in a string value
    just as strictly (`UntranslatableCharacterError`), confirmed live:
    the same 3 customer/order external_ids failed on every retry,
    forever, because nothing ever cleaned this blob. Structure (keys,
    list order, non-string values) is preserved exactly; only string
    leaves are cleaned, recursively.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {key: _sanitize_raw_payload(v) for key, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_raw_payload(v) for v in value]
    return value


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
        # max_len values match `CustomerAddress`'s actual column widths
        # (app/models/customer.py) — see `_clean_text`'s docstring.
        "line1": _clean_text(raw.get("address1"), max_len=255) or "—",
        "line2": _clean_text(raw.get("address2"), max_len=255),
        "city": _clean_text(raw.get("city"), max_len=120) or "—",
        "state": _clean_text(raw.get("province"), max_len=120),
        "country": _clean_text(raw.get("country"), max_len=120) or "India",
        "pin_code": _clean_text(raw.get("zip"), max_len=16) or "",
        "contact_name": _clean_text(name, max_len=255) or None,
        "contact_phone": _clean_text(raw.get("phone"), max_len=32),
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
        # max_len values match `Customer`'s actual column widths
        # (app/models/customer.py) — see `_clean_text`'s docstring; this
        # is the customer-side half of the Round 4 null-byte/oversized-
        # field fix (the address-side half is in `normalize_address`).
        first_name = _clean_text(raw.get("firstName"), max_len=120)
        last_name = _clean_text(raw.get("lastName"), max_len=120)
        full_name = _clean_text(
            " ".join(filter(None, [first_name, last_name])) or None, max_len=255
        )

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
            "email": _clean_text(raw.get("email"), max_len=255),
            "phone": _clean_text(raw.get("phone"), max_len=32),
            "is_active": raw.get("state") != "DISABLED",
            "external_created_at": _parse_datetime(raw.get("createdAt")),
            "external_updated_at": _parse_datetime(raw.get("updatedAt")),
            "raw_external_payload": _sanitize_raw_payload(raw),
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
            "raw_external_payload": _sanitize_raw_payload(raw),
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
            "raw_external_payload": _sanitize_raw_payload(raw),
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
            "raw_external_payload": _sanitize_raw_payload(raw),
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


# --- Refund ----------------------------------------------------------------

# `refunds/create` is the only webhook topic for this entity and Shopify
# always delivers the REST Admin API refund resource for it (there is no
# GraphQL pull-sync path for refunds in this integration, unlike
# orders/customers/products) -- this normalizer reads the REST shape
# directly, so no `webhook_shapes.py` translation step is needed.


def _refund_status(transactions: list[dict[str, Any]]) -> RefundStatus:
    statuses = {(t.get("status") or "").lower() for t in transactions if t.get("status")}
    if not statuses:
        return RefundStatus.PENDING
    if statuses & {"failure", "error"}:
        return RefundStatus.FAILED
    if statuses == {"success"}:
        return RefundStatus.COMPLETED
    return RefundStatus.PROCESSING


class ShopifyRefundNormalizer(RefundNormalizer):
    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        transactions = raw.get("transactions") or []
        line_items = raw.get("refund_line_items") or []
        if transactions:
            amount = sum((_decimal(t.get("amount")) for t in transactions), Decimal("0"))
        else:
            # No transaction data on this delivery (e.g. a store-credit-only
            # refund) -- fall back to summing the refunded line subtotals
            # rather than recording a refund of ₹0.
            amount = sum((_decimal(li.get("subtotal")) for li in line_items), Decimal("0"))

        status = _refund_status(transactions)
        created_at = _parse_datetime(raw.get("created_at"))
        processed_at = _parse_datetime(raw.get("processed_at")) or created_at

        return {
            "source_system": SourceSystem.SHOPIFY,
            "external_id": _gid_to_external_id(raw.get("id")),
            # Not this normalizer's own external_id -- consumed by
            # `app.integrations.entity_sync._upsert_refund` to resolve the
            # OMS `Order`/`Payment` this refund belongs to, then discarded
            # before the `Refund` row itself is written.
            "order_external_id": _gid_to_external_id(raw.get("order_id")),
            "amount": amount,
            "reason": _clean_text(raw.get("note"), max_len=255),
            "status": status,
            "initiated_at": created_at,
            "completed_at": processed_at if status == RefundStatus.COMPLETED else None,
            "external_created_at": created_at,
            "external_updated_at": created_at,
            "refund_metadata": {
                "restock": raw.get("restock"),
                "refund_line_items": raw.get("refund_line_items"),
            },
            "raw_external_payload": _sanitize_raw_payload(raw),
        }


CUSTOMER_NORMALIZER: Normalizer = ShopifyCustomerNormalizer()
PRODUCT_NORMALIZER: Normalizer = ShopifyProductNormalizer()
ORDER_NORMALIZER: Normalizer = ShopifyOrderNormalizer()
REFUND_NORMALIZER: Normalizer = ShopifyRefundNormalizer()

ENTITY_NORMALIZERS: dict[str, Normalizer] = {
    "customers": CUSTOMER_NORMALIZER,
    "products": PRODUCT_NORMALIZER,
    "orders": ORDER_NORMALIZER,
    "refunds": REFUND_NORMALIZER,
}
