"""IST calendar-day helpers, shared by anything that buckets/filters by
calendar day.

The OMS's business calendar day is IST, not UTC (spec: "this OMS operates
in India"), and the frontend's date-range presets (Today/Yesterday/Last 7
Days/...) are all computed against IST midnight boundaries. Every
timestamp column in this codebase (`Order.order_datetime`,
`OrderAssignment.next_follow_up_at`, etc.) is stored UTC. Bucketing by the
UTC calendar date instead of the IST one is a real, confirmed bug: any
order placed 00:00-05:29 IST has a UTC timestamp still dated the
*previous* day, so `value.date()` on the raw UTC value silently
mis-buckets ~23% of a day's rows into the wrong bucket.

This was originally a private helper inside `analytics_service.py`
(dashboard timeseries bucketing); it's factored out here so the
Telecaller "today's / overdue / upcoming follow-ups" filtering reuses the
exact same logic instead of a second, independently-maintained copy that
could silently drift and reintroduce the bug.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

IST_OFFSET = timedelta(hours=5, minutes=30)


def to_ist(value: datetime) -> datetime:
    """Shifts a UTC-aware datetime to the IST wall-clock moment, keeping it
    tz-aware (still stamped UTC) — sufficient for reading off calendar-date
    components, without needing a timezone database dependency for a fixed
    (no-DST) offset like IST.
    """
    return value.astimezone(UTC) + IST_OFFSET


def ist_day_bounds(reference: datetime | None = None) -> tuple[datetime, datetime]:
    """Returns `(start_of_today_utc, start_of_tomorrow_utc)` for the IST
    calendar day containing `reference` (defaults to now) — i.e. the UTC
    instants that bound "today" when "today" means an IST calendar day.
    Comparing a UTC column against these two aware datetimes is
    dialect-portable (no `AT TIME ZONE`/`date_trunc` needed) and correct
    regardless of which dialect stores the column.
    """
    now = reference or datetime.now(UTC)
    ist_now = to_ist(now)
    ist_midnight = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_today_utc = ist_midnight - IST_OFFSET
    start_of_tomorrow_utc = start_of_today_utc + timedelta(days=1)
    return start_of_today_utc, start_of_tomorrow_utc
