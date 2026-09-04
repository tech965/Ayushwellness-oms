"""Natural-language date ranges, resolved in the OMS business timezone.

The OMS operates in India: a "calendar day" is an **IST** day, and the
dashboard's own date presets are computed against IST midnight
boundaries (see `app.core.timezone` and
`app.services.analytics_service._bucket_key`). This module is the chat
assistant's single source of truth for turning a phrase like "yesterday"
or "last week" into the concrete `[date_from, date_to]` UTC instants the
analytics/orders services expect — using the exact same IST offset as the
rest of the codebase, so a chat answer and the dashboard can never
disagree about where a day starts.

Every returned bound is a timezone-aware UTC datetime. `date_to` is the
**inclusive** end (`... 23:59:59.999999` of the last IST day in the
range, expressed in UTC), matching how the analytics service filters
(`Order.order_datetime <= date_to`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.core.timezone import IST_OFFSET, to_ist

# Every preset the resolver understands.
PRESETS: tuple[str, ...] = (
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "this_year",
    "last_year",
    "last_7_days",
    "last_30_days",
    "last_90_days",
    "last_24_hours",
    "week_to_date",
    "month_to_date",
    "year_to_date",
    "all_time",
)

# The shorter subset advertised to the model in the tool schema (keeps the
# per-request token cost down). The resolver still accepts anything in
# PRESETS if the model passes it.
COMMON_PRESETS: tuple[str, ...] = (
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "last_7_days",
    "last_30_days",
    "this_year",
)

_EARLIEST = datetime(2000, 1, 1, tzinfo=UTC)


class DateRangeError(ValueError):
    """Raised when a caller passes an unparseable preset or explicit range."""


@dataclass(frozen=True)
class ResolvedRange:
    date_from: datetime
    date_to: datetime
    label: str

    def as_query_params(self) -> dict[str, str]:
        return {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }


def _ist_midnight_utc(d: date) -> datetime:
    """The UTC instant of 00:00:00 IST on the given IST calendar date."""
    return datetime(d.year, d.month, d.day, tzinfo=UTC) - IST_OFFSET


def _end_of_ist_day_utc(d: date) -> datetime:
    """The UTC instant of 23:59:59.999999 IST on the given IST date."""
    return _ist_midnight_utc(d) + timedelta(days=1) - timedelta(microseconds=1)


def _ist_today(now: datetime | None) -> date:
    return to_ist(now or datetime.now(UTC)).date()


def resolve_preset(preset: str, *, now: datetime | None = None) -> ResolvedRange:
    preset = preset.strip().lower().replace("-", "_").replace(" ", "_")
    today = _ist_today(now)
    ref_now = (now or datetime.now(UTC)).astimezone(UTC)

    if preset == "all_time":
        return ResolvedRange(_EARLIEST, _end_of_ist_day_utc(today), "all time")

    if preset == "today":
        return ResolvedRange(_ist_midnight_utc(today), _end_of_ist_day_utc(today), "today")

    if preset == "yesterday":
        y = today - timedelta(days=1)
        return ResolvedRange(_ist_midnight_utc(y), _end_of_ist_day_utc(y), "yesterday")

    if preset == "last_24_hours":
        return ResolvedRange(ref_now - timedelta(hours=24), ref_now, "the last 24 hours")

    if preset in ("this_week", "week_to_date"):
        # Week starts Monday, matching analytics_service._bucket_key.
        start = today - timedelta(days=today.weekday())
        end_day = today if preset == "week_to_date" else start + timedelta(days=6)
        label = "this week so far" if preset == "week_to_date" else "this week"
        return ResolvedRange(_ist_midnight_utc(start), _end_of_ist_day_utc(end_day), label)

    if preset == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        end_day = start + timedelta(days=6)
        return ResolvedRange(_ist_midnight_utc(start), _end_of_ist_day_utc(end_day), "last week")

    if preset in ("this_month", "month_to_date"):
        start = today.replace(day=1)
        end_day = today if preset == "month_to_date" else _last_day_of_month(today)
        label = "this month so far" if preset == "month_to_date" else "this month"
        return ResolvedRange(_ist_midnight_utc(start), _end_of_ist_day_utc(end_day), label)

    if preset == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        start = last_prev.replace(day=1)
        return ResolvedRange(_ist_midnight_utc(start), _end_of_ist_day_utc(last_prev), "last month")

    if preset in ("this_year", "year_to_date"):
        start = today.replace(month=1, day=1)
        end_day = today if preset == "year_to_date" else today.replace(month=12, day=31)
        label = "this year so far" if preset == "year_to_date" else "this year"
        return ResolvedRange(_ist_midnight_utc(start), _end_of_ist_day_utc(end_day), label)

    if preset == "last_year":
        start = today.replace(year=today.year - 1, month=1, day=1)
        end_day = today.replace(year=today.year - 1, month=12, day=31)
        return ResolvedRange(_ist_midnight_utc(start), _end_of_ist_day_utc(end_day), "last year")

    for n in (7, 30, 90):
        if preset == f"last_{n}_days":
            start = today - timedelta(days=n - 1)
            return ResolvedRange(
                _ist_midnight_utc(start),
                _end_of_ist_day_utc(today),
                f"the last {n} days",
            )

    raise DateRangeError(f"Unknown date preset '{preset}'. Valid presets: {', '.join(PRESETS)}.")


def _last_day_of_month(d: date) -> date:
    if d.month == 12:
        return d.replace(day=31)
    return d.replace(month=d.month + 1, day=1) - timedelta(days=1)


def _parse_explicit(value: str, *, is_end: bool) -> datetime:
    raw = value.strip()
    # Bare calendar date -> snap to the IST day boundary.
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        d = None
    if d is not None:
        return _end_of_ist_day_utc(d) if is_end else _ist_midnight_utc(d)

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DateRangeError(
            f"Could not parse date/time '{value}'. Use YYYY-MM-DD or an ISO-8601 timestamp."
        ) from exc
    # A naive timestamp is interpreted as IST wall-clock, then converted to UTC.
    if dt.tzinfo is None:
        return (dt - IST_OFFSET).replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def resolve(
    *,
    preset: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    now: datetime | None = None,
    default_preset: str = "today",
) -> ResolvedRange:
    """Resolve a tool's date arguments to a concrete UTC range.

    Precedence: an explicit `date_from`/`date_to` pair wins over `preset`;
    `preset` wins over the `default_preset`. A lone `date_from` (no
    `date_to`) runs through "now"; a lone `date_to` runs from the earliest
    supported date.
    """
    if date_from or date_to:
        start = _parse_explicit(date_from, is_end=False) if date_from else _EARLIEST
        end = (
            _parse_explicit(date_to, is_end=True)
            if date_to
            else (now or datetime.now(UTC)).astimezone(UTC)
        )
        if end < start:
            raise DateRangeError("date_to is before date_from.")
        return ResolvedRange(start, end, _explicit_label(start, end))

    return resolve_preset(preset or default_preset, now=now)


def _explicit_label(start: datetime, end: datetime) -> str:
    s = to_ist(start).strftime("%d %b %Y")
    e = to_ist(end).strftime("%d %b %Y")
    return s if s == e else f"{s} to {e}"


def previous_period(rng: ResolvedRange) -> ResolvedRange:
    """The immediately preceding window of equal length — the basis for
    "compared with ..." deltas. Mirrors
    `analytics_service._previous_range`.
    """
    # date_to is the *inclusive* end (…999999), so the true duration is
    # one microsecond more than the raw difference. Restore it before
    # shifting back, otherwise the previous window drifts 1µs forward each
    # period and "previous of today" no longer lines up with "yesterday".
    span = rng.date_to - rng.date_from + timedelta(microseconds=1)
    return ResolvedRange(
        rng.date_from - span,
        rng.date_from - timedelta(microseconds=1),
        f"the preceding {_humanize_span(span)}",
    )


def _humanize_span(span: timedelta) -> str:
    days = max(1, round(span.total_seconds() / 86400))
    return "day" if days == 1 else f"{days} days"
