"""Abandoned-checkout sync: GraphQL-node normalization and idempotent
upsert. Mirrors the existing `ShopifyOrderNormalizer`/`OrderService.
upsert_synced_order` test conventions — real DB rows via `AsyncSession`,
no live Shopify call (`ABANDONED_CHECKOUTS_QUERY` itself is exercised only
by `ShopifyAdapter.fetch`, already covered generically for every entity
type by the existing adapter tests).
"""

from __future__ import annotations

import pytest
from app.integrations.shopify.normalizer import ShopifyAbandonedCheckoutNormalizer
from app.services.abandoned_checkout_service import AbandonedCheckoutService
from sqlalchemy.ext.asyncio import AsyncSession

RAW_CHECKOUT_NODE = {
    "id": "gid://shopify/AbandonedCheckout/123456",
    "name": "#C1001",
    "email": "shopper@example.com",
    "phone": "+91 98765 43210",
    "abandonedCheckoutUrl": "https://example.myshopify.com/checkout/abc",
    "completedAt": None,
    "createdAt": "2026-09-01T10:00:00Z",
    "updatedAt": "2026-09-01T10:05:00Z",
    "totalPriceSet": {"shopMoney": {"amount": "1499.00"}},
    "subtotalPriceSet": {"shopMoney": {"amount": "1399.00"}},
    "customer": {
        "id": "gid://shopify/Customer/999",
        "firstName": "Asha",
        "lastName": "Rao",
        "email": "asha@example.com",
        "phone": None,
    },
    "billingAddress": {"name": "Asha Rao", "phone": None},
    "lineItems": {
        "edges": [
            {"node": {"title": "Ashwagandha Capsules", "quantity": 2}},
            {"node": {"title": "Multivitamin", "quantity": 1}},
        ]
    },
}


def test_normalizer_extracts_contact_and_line_items() -> None:
    normalized = ShopifyAbandonedCheckoutNormalizer().normalize(RAW_CHECKOUT_NODE)

    assert normalized["external_id"] == "123456"
    assert normalized["customer_phone"] == "+91 98765 43210"  # checkout-level phone wins
    assert normalized["customer_email"] == "shopper@example.com"  # checkout-level email wins
    assert normalized["is_recovered"] is False
    assert normalized["total_amount"] == pytest.approx(1499.00)
    assert normalized["subtotal_amount"] == pytest.approx(1399.00)
    assert len(normalized["line_items"]) == 2
    assert normalized["line_items"][0] == {"title": "Ashwagandha Capsules", "quantity": 2}


def test_normalizer_falls_back_to_customer_contact_when_checkout_level_is_absent() -> None:
    raw = dict(RAW_CHECKOUT_NODE, email=None, phone=None)
    raw["billingAddress"] = {"name": "Asha Rao", "phone": "9000000000"}
    normalized = ShopifyAbandonedCheckoutNormalizer().normalize(raw)

    assert normalized["customer_email"] == "asha@example.com"
    assert normalized["customer_phone"] == "9000000000"


def test_normalizer_never_fabricates_contact_info_when_none_exists() -> None:
    """Spec's hard safety rule: no phone, no email anywhere on the
    checkout -> both stay `None`, never a placeholder value."""
    raw = dict(RAW_CHECKOUT_NODE, email=None, phone=None, customer={}, billingAddress={})
    normalized = ShopifyAbandonedCheckoutNormalizer().normalize(raw)

    assert normalized["customer_phone"] is None
    assert normalized["customer_email"] is None


def test_normalizer_marks_completed_checkout_as_recovered() -> None:
    raw = dict(RAW_CHECKOUT_NODE, completedAt="2026-09-02T08:00:00Z")
    normalized = ShopifyAbandonedCheckoutNormalizer().normalize(raw)
    assert normalized["is_recovered"] is True


async def test_upsert_synced_checkout_is_idempotent(db_session: AsyncSession) -> None:
    service = AbandonedCheckoutService(db_session)
    normalized = ShopifyAbandonedCheckoutNormalizer().normalize(RAW_CHECKOUT_NODE)

    checkout, created = await service.upsert_synced_checkout(**dict(normalized))
    assert created is True
    assert checkout.customer_phone == "+91 98765 43210"

    # A second sync of the exact same node must update the same row, not
    # create a duplicate (source_system, external_id) is the identity).
    normalized_again = ShopifyAbandonedCheckoutNormalizer().normalize(RAW_CHECKOUT_NODE)
    checkout_2, created_2 = await service.upsert_synced_checkout(**dict(normalized_again))
    assert created_2 is False
    assert checkout_2.id == checkout.id


async def test_upsert_synced_checkout_drops_stale_out_of_order_delivery(
    db_session: AsyncSession,
) -> None:
    service = AbandonedCheckoutService(db_session)
    newer = ShopifyAbandonedCheckoutNormalizer().normalize(RAW_CHECKOUT_NODE)
    checkout, _ = await service.upsert_synced_checkout(**dict(newer))
    assert checkout.customer_email == "shopper@example.com"

    older_raw = dict(RAW_CHECKOUT_NODE, updatedAt="2026-08-01T00:00:00Z", email="stale@example.com")
    older = ShopifyAbandonedCheckoutNormalizer().normalize(older_raw)
    checkout_after, _ = await service.upsert_synced_checkout(**dict(older))

    # The stale (older external_updated_at) payload must never overwrite
    # the newer stored data.
    assert checkout_after.customer_email == "shopper@example.com"
