"""Pure, stateless telecalling lead classification — no I/O, so every
caller (team.py's/telecaller.py's response builders, `TelecallingService`'s
dashboard aggregates) computes the exact same category/priority for the
exact same order/checkout state. Never persisted (see
`app.models.enums.LeadCategory`/`LeadPriority`'s docstrings) — reuses the
OMS's own existing `PaymentType`/`FulfillmentStatus` classification
(`app.integrations.shopify.normalizer.normalize_payment_type` is the one
place that ever decides COD vs. prepaid; this module only reads the
already-decided value).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.enums import FulfillmentStatus, LeadCategory, LeadPriority, PaymentType

# How many days old an order/checkout still counts as "recent" for the
# MEDIUM-priority bucket (spec: "Recent abandoned cart / Recent COD
# order"). The spec gives no exact number — 7 days is a reasonable
# default for a first-purchase-experience follow-up window; adjust here
# if the business wants a different cutoff, not per-caller.
RECENT_WINDOW_DAYS = 7


def classify_order(
    *, payment_type: PaymentType, fulfillment_status: FulfillmentStatus
) -> LeadCategory | None:
    """`None` for `PaymentType.OTHER` — not one of the spec's defined
    order-based categories (COD Unfulfilled / COD Fulfilled / Prepaid), so
    it's deliberately excluded from the telecalling Lead Pool rather than
    mis-bucketed into COD or Prepaid.
    """
    if payment_type == PaymentType.COD:
        return (
            LeadCategory.COD_UNFULFILLED
            if fulfillment_status == FulfillmentStatus.UNFULFILLED
            else LeadCategory.COD_FULFILLED
        )
    if payment_type == PaymentType.PREPAID:
        return LeadCategory.PREPAID
    return None


def classify_priority(
    *,
    category: LeadCategory,
    next_follow_up_at: datetime | None,
    reference_datetime: datetime,
    now: datetime | None = None,
) -> LeadPriority:
    """Spec's "Lead Priority" rules, reused identically for an order-based
    lead and a checkout-based lead:

    HIGH   — Abandoned Checkout, COD Unfulfilled, or any callback/
             follow-up that's due today or overdue.
    MEDIUM — a recent (`RECENT_WINDOW_DAYS`) COD Fulfilled order — a
             fresh post-delivery follow-up opportunity.
    LOW    — everything else (older COD Fulfilled, and Prepaid
             follow-ups generally — spec: "older fulfilled/prepaid
             follow-up").

    A due/overdue follow-up always wins regardless of category — it can
    only ever raise priority to HIGH, never lower a category that's
    already HIGH on its own.
    """
    now = now or datetime.now(UTC)
    if next_follow_up_at is not None and next_follow_up_at <= now:
        return LeadPriority.HIGH
    if category in (LeadCategory.ABANDONED_CHECKOUT, LeadCategory.COD_UNFULFILLED):
        return LeadPriority.HIGH
    is_recent = (now - reference_datetime) <= timedelta(days=RECENT_WINDOW_DAYS)
    if category == LeadCategory.COD_FULFILLED and is_recent:
        return LeadPriority.MEDIUM
    return LeadPriority.LOW
