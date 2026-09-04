"""IST date-range resolution for the AI assistant.

The OMS business day is an Asia/Kolkata (UTC+5:30) calendar day. Every
assertion here pins the exact UTC instants a phrase resolves to, because
a wrong boundary silently mis-buckets ~23% of a day's orders (see
`app.core.timezone`).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.chat import datetime_ranges as dr

# A fixed "now": 2026-09-02 10:00 IST  ==  2026-09-02 04:30 UTC.
NOW = datetime(2026, 9, 2, 4, 30, tzinfo=UTC)


def test_today_spans_the_ist_calendar_day_in_utc() -> None:
    rng = dr.resolve_preset("today", now=NOW)
    # 2026-09-02 00:00:00 IST -> 2026-09-01 18:30:00 UTC
    assert rng.date_from == datetime(2026, 9, 1, 18, 30, 0, tzinfo=UTC)
    # 2026-09-02 23:59:59.999999 IST -> 2026-09-02 18:29:59.999999 UTC
    assert rng.date_to == datetime(2026, 9, 2, 18, 29, 59, 999999, tzinfo=UTC)
    assert rng.label == "today"


def test_today_matches_the_dashboard_date_boundary_example() -> None:
    """PHASE 18: the live dashboard uses
    date_from=2026-09-01T18:30:00.000Z / date_to=2026-09-02T18:29:59.999Z
    for an India business day. The assistant must agree.
    """
    rng = dr.resolve_preset("today", now=NOW)
    assert rng.date_from.isoformat() == "2026-09-01T18:30:00+00:00"
    assert rng.date_from == datetime.fromisoformat(
        "2026-09-01T18:30:00.000Z".replace("Z", "+00:00")
    )
    assert rng.date_to.replace(microsecond=0) == datetime.fromisoformat(
        "2026-09-02T18:29:59.000Z".replace("Z", "+00:00")
    )


def test_yesterday() -> None:
    rng = dr.resolve_preset("yesterday", now=NOW)
    assert rng.date_from == datetime(2026, 8, 31, 18, 30, tzinfo=UTC)
    assert rng.date_to == datetime(2026, 9, 1, 18, 29, 59, 999999, tzinfo=UTC)


def test_early_morning_ist_still_resolves_to_the_same_ist_day() -> None:
    # 2026-09-02 01:00 IST == 2026-09-01 19:30 UTC — a UTC-based "today"
    # would wrongly say 2026-09-01.
    early = datetime(2026, 9, 1, 19, 30, tzinfo=UTC)
    rng = dr.resolve_preset("today", now=early)
    assert rng.date_from == datetime(2026, 9, 1, 18, 30, tzinfo=UTC)
    assert rng.date_to == datetime(2026, 9, 2, 18, 29, 59, 999999, tzinfo=UTC)


def test_this_week_starts_monday_ist() -> None:
    # 2026-09-02 is a Wednesday; the ISO week starts Monday 2026-08-31.
    rng = dr.resolve_preset("this_week", now=NOW)
    assert rng.date_from == datetime(2026, 8, 30, 18, 30, tzinfo=UTC)  # 31 Aug 00:00 IST
    assert rng.date_to == datetime(2026, 9, 6, 18, 29, 59, 999999, tzinfo=UTC)  # 6 Sep 23:59 IST


def test_last_week() -> None:
    rng = dr.resolve_preset("last_week", now=NOW)
    assert rng.date_from == datetime(2026, 8, 23, 18, 30, tzinfo=UTC)  # Mon 24 Aug 00:00 IST
    assert rng.date_to == datetime(2026, 8, 30, 18, 29, 59, 999999, tzinfo=UTC)  # Sun 30 Aug


def test_last_7_days_is_7_ist_days_inclusive() -> None:
    rng = dr.resolve_preset("last_7_days", now=NOW)
    # 27 Aug 00:00 IST .. 2 Sep 23:59 IST
    assert rng.date_from == datetime(2026, 8, 26, 18, 30, tzinfo=UTC)
    assert rng.date_to == datetime(2026, 9, 2, 18, 29, 59, 999999, tzinfo=UTC)


def test_this_month_and_last_month() -> None:
    this_month = dr.resolve_preset("this_month", now=NOW)
    assert this_month.date_from == datetime(2026, 8, 31, 18, 30, tzinfo=UTC)  # 1 Sep 00:00 IST
    last_month = dr.resolve_preset("last_month", now=NOW)
    assert last_month.date_from == datetime(2026, 7, 31, 18, 30, tzinfo=UTC)  # 1 Aug 00:00 IST
    assert last_month.date_to == datetime(2026, 8, 31, 18, 29, 59, 999999, tzinfo=UTC)  # 31 Aug


def test_explicit_range_between_two_dates() -> None:
    rng = dr.resolve(date_from="2026-09-01", date_to="2026-09-02", now=NOW)
    assert rng.date_from == datetime(2026, 8, 31, 18, 30, tzinfo=UTC)
    assert rng.date_to == datetime(2026, 9, 2, 18, 29, 59, 999999, tzinfo=UTC)


def test_explicit_range_overrides_preset() -> None:
    rng = dr.resolve(preset="today", date_from="2026-01-01", date_to="2026-01-31", now=NOW)
    assert rng.date_from == datetime(2025, 12, 31, 18, 30, tzinfo=UTC)


def test_naive_timestamp_is_treated_as_ist_wall_clock() -> None:
    rng = dr.resolve(date_from="2026-09-02T09:00:00", date_to="2026-09-02T18:00:00", now=NOW)
    assert rng.date_from == datetime(2026, 9, 2, 3, 30, tzinfo=UTC)
    assert rng.date_to == datetime(2026, 9, 2, 12, 30, tzinfo=UTC)


def test_previous_period_of_today_is_yesterday() -> None:
    today = dr.resolve_preset("today", now=NOW)
    prev = dr.previous_period(today)
    yesterday = dr.resolve_preset("yesterday", now=NOW)
    assert prev.date_from == yesterday.date_from
    # previous_period ends 1µs before today starts == yesterday's inclusive end.
    assert prev.date_to == yesterday.date_to


def test_unknown_preset_raises() -> None:
    with pytest.raises(dr.DateRangeError):
        dr.resolve_preset("since_diwali", now=NOW)


def test_reversed_explicit_range_raises() -> None:
    with pytest.raises(dr.DateRangeError):
        dr.resolve(date_from="2026-09-02", date_to="2026-09-01", now=NOW)


def test_all_presets_resolve() -> None:
    for preset in dr.PRESETS:
        rng = dr.resolve_preset(preset, now=NOW)
        assert rng.date_from <= rng.date_to
