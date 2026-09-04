"""Unit tests for `app.services.lead_classification` — pure functions, no
DB/HTTP involved. Covers the spec's explicit distinctions: COD Unfulfilled
!= COD Fulfilled != Prepaid, and the HIGH/MEDIUM/LOW priority rules.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.enums import FulfillmentStatus, LeadCategory, LeadPriority, PaymentType
from app.services.lead_classification import classify_order, classify_priority


def test_cod_unfulfilled_is_its_own_category() -> None:
    category = classify_order(
        payment_type=PaymentType.COD, fulfillment_status=FulfillmentStatus.UNFULFILLED
    )
    assert category == LeadCategory.COD_UNFULFILLED


def test_cod_fulfilled_is_a_distinct_category_from_cod_unfulfilled() -> None:
    category = classify_order(
        payment_type=PaymentType.COD, fulfillment_status=FulfillmentStatus.FULFILLED
    )
    assert category == LeadCategory.COD_FULFILLED
    assert category != LeadCategory.COD_UNFULFILLED


def test_cod_partial_fulfillment_counts_as_cod_fulfilled_bucket() -> None:
    """Only UNFULFILLED is the "still needs confirmation" state — PARTIAL
    is operationally closer to FULFILLED for calling purposes (spec only
    defines Unfulfilled vs. Fulfilled, no third COD bucket).
    """
    assert (
        classify_order(payment_type=PaymentType.COD, fulfillment_status=FulfillmentStatus.PARTIAL)
        == LeadCategory.COD_FULFILLED
    )


def test_prepaid_is_never_mixed_with_cod() -> None:
    for fulfillment_status in FulfillmentStatus:
        category = classify_order(
            payment_type=PaymentType.PREPAID, fulfillment_status=fulfillment_status
        )
        assert category == LeadCategory.PREPAID
        assert category not in (LeadCategory.COD_UNFULFILLED, LeadCategory.COD_FULFILLED)


def test_other_payment_type_has_no_defined_category() -> None:
    category = classify_order(
        payment_type=PaymentType.OTHER, fulfillment_status=FulfillmentStatus.UNFULFILLED
    )
    assert category is None


def test_cod_unfulfilled_priority_is_high() -> None:
    now = datetime.now(UTC)
    priority = classify_priority(
        category=LeadCategory.COD_UNFULFILLED,
        next_follow_up_at=None,
        reference_datetime=now,
        now=now,
    )
    assert priority == LeadPriority.HIGH


def test_abandoned_checkout_priority_is_high() -> None:
    now = datetime.now(UTC)
    priority = classify_priority(
        category=LeadCategory.ABANDONED_CHECKOUT,
        next_follow_up_at=None,
        reference_datetime=now,
        now=now,
    )
    assert priority == LeadPriority.HIGH


def test_overdue_follow_up_forces_high_regardless_of_category() -> None:
    now = datetime.now(UTC)
    priority = classify_priority(
        category=LeadCategory.PREPAID,
        next_follow_up_at=now - timedelta(hours=1),
        reference_datetime=now - timedelta(days=30),
        now=now,
    )
    assert priority == LeadPriority.HIGH


def test_recent_cod_fulfilled_is_medium() -> None:
    now = datetime.now(UTC)
    priority = classify_priority(
        category=LeadCategory.COD_FULFILLED,
        next_follow_up_at=None,
        reference_datetime=now - timedelta(days=1),
        now=now,
    )
    assert priority == LeadPriority.MEDIUM


def test_old_prepaid_follow_up_is_low() -> None:
    now = datetime.now(UTC)
    priority = classify_priority(
        category=LeadCategory.PREPAID,
        next_follow_up_at=None,
        reference_datetime=now - timedelta(days=60),
        now=now,
    )
    assert priority == LeadPriority.LOW


def test_old_cod_fulfilled_is_low_not_medium() -> None:
    now = datetime.now(UTC)
    priority = classify_priority(
        category=LeadCategory.COD_FULFILLED,
        next_follow_up_at=None,
        reference_datetime=now - timedelta(days=30),
        now=now,
    )
    assert priority == LeadPriority.LOW
