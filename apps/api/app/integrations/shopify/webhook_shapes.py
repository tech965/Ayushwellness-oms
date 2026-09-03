"""Shopify webhook payload -> GraphQL node shape.

Round 4 finding: `app/integrations/shopify/normalizer.py` was built
exclusively for the GraphQL node shape the pull-sync adapter fetches
(camelCase keys, `{"shopMoney": {"amount": ...}}` money sets, `{"edges":
[{"node": ...}]}` connections, `"gid://shopify/Order/123"` string ids).
Shopify webhook deliveries are a *different* shape — the classic REST
Admin API resource representation (snake_case keys, flat decimal-string
prices, a flat `line_items`/`variants` array, and a plain integer `id`)
— documented behavior, not a per-store quirk: every Shopify webhook
payload uses this shape regardless of whether the subscription was
created via the REST or GraphQL Admin API.

Confirmed live (see the Round 4 QA report): feeding a real REST-shaped
orders/create payload straight into `ShopifyOrderNormalizer.normalize()`
raised `TypeError` (`"/" in <int>`) before this module existed, because
`raw.get("id")` is an `int`, not a `"gid://..."` string. Every one of
the 7 webhooks registered for this store would have failed on every
single delivery.

This module's only job is translating the REST shape into the exact
GraphQL node shape the existing (unmodified, still fully pull-sync-
correct) normalizers already expect — so the fix lives entirely here
plus one defensive one-liner in `normalizer.py`
(`_gid_to_external_id` now coerces to `str` first). Nothing about the
GraphQL pull-sync path changes.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _gid(resource: str, raw_id: Any) -> str | None:
    """`123456` -> `"gid://shopify/Order/123456"` — lets the existing,
    unmodified `_gid_to_external_id()` extract the id exactly as it does
    for a real GraphQL response, and lets id-equality checks (e.g.
    "is this address the customer's default one") keep working the same
    way whether the raw ids are GraphQL GIDs or REST integers.
    """
    if raw_id is None:
        return None
    return f"gid://shopify/{resource}/{raw_id}"


def _amount(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"shopMoney": {"amount": str(value)}}


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("0")


def _rest_address_to_graphql(
    raw: dict[str, Any] | None, *, resource: str = "MailingAddress"
) -> dict[str, Any] | None:
    """REST `Address`/`CustomerAddress` already uses the same field names
    (`address1`, `address2`, `city`, `province`, `zip`, `country`,
    `phone`, `name`) as the GraphQL `MailingAddress` fields
    `normalize_address()` reads — only `id` needs GID-wrapping.
    """
    if not raw:
        return None
    return {**raw, "id": _gid(resource, raw.get("id"))}


# Shopify REST `fulfillment_status` values -> the GraphQL
# `displayFulfillmentStatus` vocabulary `normalize_fulfillment_status()`
# already maps from. Not a simple case-fold: REST's "partial" is spelled
# "PARTIALLY_FULFILLED" on the GraphQL side.
_REST_FULFILLMENT_STATUS_TO_GRAPHQL: dict[str | None, str] = {
    None: "UNFULFILLED",
    "fulfilled": "FULFILLED",
    "partial": "PARTIALLY_FULFILLED",
    "restocked": "RESTOCKED",
}


def order_webhook_to_graphql_shape(raw: dict[str, Any]) -> dict[str, Any]:
    customer = raw.get("customer") or {}
    line_items = raw.get("line_items") or []
    shipping_lines = raw.get("shipping_lines") or []
    first_shipping_line = shipping_lines[0] if shipping_lines else {}

    return {
        "id": _gid("Order", raw.get("id")),
        "name": raw.get("name"),
        "createdAt": raw.get("created_at"),
        "updatedAt": raw.get("updated_at"),
        "cancelledAt": raw.get("cancelled_at"),
        "currencyCode": (raw.get("currency") or "").upper() or None,
        # financial_status vocabulary already matches displayFinancialStatus
        # case-insensitively ("partially_paid" -> "PARTIALLY_PAID"); the
        # normalizer upper()s it, so no remapping needed here.
        "displayFinancialStatus": raw.get("financial_status"),
        "displayFulfillmentStatus": _REST_FULFILLMENT_STATUS_TO_GRAPHQL.get(
            raw.get("fulfillment_status"), "UNFULFILLED"
        ),
        # REST gives a comma-joined string for tags (same shape as the
        # product webhook below); `note` is already a plain string on
        # both shapes -- `_normalize_tags`/`_clean_text` in normalizer.py
        # accept either shape unchanged.
        "tags": raw.get("tags"),
        "note": raw.get("note"),
        "paymentGatewayNames": raw.get("payment_gateway_names"),
        "customer": {"id": _gid("Customer", customer.get("id"))} if customer.get("id") else {},
        "subtotalPriceSet": _amount(raw.get("subtotal_price")),
        "totalDiscountsSet": _amount(raw.get("total_discounts")),
        "totalTaxSet": _amount(raw.get("total_tax")),
        "totalPriceSet": _amount(raw.get("total_price")),
        "shippingLine": {"originalPriceSet": _amount(first_shipping_line.get("price"))}
        if first_shipping_line
        else None,
        "shippingAddress": _rest_address_to_graphql(raw.get("shipping_address")),
        "billingAddress": _rest_address_to_graphql(raw.get("billing_address")),
        "lineItems": {
            "edges": [{"node": _order_line_item_to_graphql_shape(item)} for item in line_items]
        },
        "fulfillments": [
            _fulfillment_to_graphql_shape(f) for f in (raw.get("fulfillments") or [])
        ],
    }


# REST `Fulfillment.shipment_status` values ("confirmed"/"in_transit"/
# "delivered"/"failure"/... — see shopify.dev's REST Fulfillment resource)
# already match `FulfillmentDisplayStatus`'s GraphQL enum spelling
# case-insensitively; the normalizer upper()s/lower()s consistently with
# every other status map in this module, so a plain uppercase is enough.
def _fulfillment_to_graphql_shape(raw: dict[str, Any]) -> dict[str, Any]:
    shipment_status = raw.get("shipment_status")
    return {"displayStatus": shipment_status.upper() if shipment_status else None}


def _order_line_item_to_graphql_shape(raw: dict[str, Any]) -> dict[str, Any]:
    quantity = raw.get("quantity") or 0
    unit_price = _dec(raw.get("price"))
    # REST gives the line's total *discount*; the normalizer wants the
    # after-discount *total* (`discountedTotalSet`) to derive discount
    # from — see the docstring on `ShopifyOrderNormalizer._normalize_line_item`
    # for why this direction of derivation matters (it's the fix for the
    # earlier "Total ₹0.00" bug; don't invert it here).
    line_discount = _dec(raw.get("total_discount"))
    after_discount_total = max(Decimal("0"), unit_price * quantity - line_discount)
    variant_id = raw.get("variant_id")

    return {
        "id": _gid("LineItem", raw.get("id")),
        "sku": raw.get("sku"),
        "title": raw.get("title") or raw.get("name"),
        "quantity": quantity,
        "originalUnitPriceSet": _amount(raw.get("price")),
        "discountedTotalSet": _amount(str(after_discount_total)),
        "variant": {"id": _gid("ProductVariant", variant_id)} if variant_id else {},
    }


# Shopify REST `Customer.state` ("disabled"/"invited"/"enabled"/
# "declined") vs the GraphQL `CustomerState` enum ("DISABLED"/...) —
# `normalize()` compares `raw.get("state") != "DISABLED"` case-sensitively
# with no internal upper(), unlike the payment/fulfillment status maps,
# so this one does need an explicit case-fold here.
def customer_webhook_to_graphql_shape(raw: dict[str, Any]) -> dict[str, Any]:
    default_address = raw.get("default_address")
    addresses = raw.get("addresses") or []

    return {
        "id": _gid("Customer", raw.get("id")),
        "firstName": raw.get("first_name"),
        "lastName": raw.get("last_name"),
        "email": raw.get("email"),
        "phone": raw.get("phone"),
        "state": (raw.get("state") or "").upper() or None,
        "createdAt": raw.get("created_at"),
        "updatedAt": raw.get("updated_at"),
        "defaultAddress": _rest_address_to_graphql(default_address, resource="MailingAddress"),
        "addresses": [
            addr
            for addr in (_rest_address_to_graphql(a, resource="MailingAddress") for a in addresses)
            if addr is not None
        ],
    }


def product_webhook_to_graphql_shape(raw: dict[str, Any]) -> dict[str, Any]:
    # REST `options`: [{"name": "Size", "position": 1, "values": [...]}, ...]
    # REST `variants[i]`: flat `option1`/`option2`/`option3`, not a
    # `selectedOptions` list — reconstruct it by position against the
    # product-level option names (fall back to a generic label if the
    # product's `options` array wasn't included on this payload).
    option_names = {opt.get("position"): opt.get("name") for opt in (raw.get("options") or [])}

    return {
        "id": _gid("Product", raw.get("id")),
        "title": raw.get("title"),
        "descriptionHtml": raw.get("body_html"),
        "vendor": raw.get("vendor"),
        "productType": raw.get("product_type"),
        # REST gives a comma-joined string; normalizer already accepts either shape
        "tags": raw.get("tags"),
        # normalizer upper()s this; REST/GraphQL vocab matches case-insensitively
        "status": raw.get("status"),
        "createdAt": raw.get("created_at"),
        "updatedAt": raw.get("updated_at"),
        "variants": {
            "edges": [
                {"node": _product_variant_to_graphql_shape(v, option_names)}
                for v in (raw.get("variants") or [])
            ]
        },
    }


def _product_variant_to_graphql_shape(
    raw: dict[str, Any], option_names: dict[int, str]
) -> dict[str, Any]:
    selected_options = [
        {
            "name": option_names.get(position, f"Option {position}"),
            "value": raw.get(f"option{position}"),
        }
        for position in (1, 2, 3)
        if raw.get(f"option{position}") is not None
    ]

    return {
        "id": _gid("ProductVariant", raw.get("id")),
        "sku": raw.get("sku"),
        "title": raw.get("title"),
        "price": raw.get("price"),
        "compareAtPrice": raw.get("compare_at_price"),
        "inventoryQuantity": raw.get("inventory_quantity"),
        "weight": raw.get("weight") or raw.get("grams"),
        "barcode": raw.get("barcode"),
        "selectedOptions": selected_options,
    }


WEBHOOK_SHAPE_TRANSLATORS = {
    "orders": order_webhook_to_graphql_shape,
    "customers": customer_webhook_to_graphql_shape,
    "products": product_webhook_to_graphql_shape,
}
