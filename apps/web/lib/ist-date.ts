/** IST calendar-day boundary helpers for the frontend, mirroring
 * `app/core/timezone.py` (backend) — same fixed +5:30 offset, same
 * "IST calendar day" definition. The dashboard's date-range presets
 * (Today/Yesterday/This Week/This Month/Custom Range) must resolve to
 * the same UTC instants regardless of which timezone the viewer's
 * browser/OS happens to be set to; computing them with `new Date()` +
 * date-fns' local-time helpers (the previous implementation) silently
 * breaks the moment a browser isn't set to IST, reintroducing the exact
 * UTC/IST bucketing bug `app/core/timezone.py`'s docstring documents on
 * the backend. There is deliberately no second, independent timezone
 * implementation here — this is a straight port of the same fixed-offset
 * logic, since India has no DST and a tz-database dependency isn't
 * needed for it.
 */

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000
const DAY_MS = 24 * 60 * 60 * 1000

/** Returns the UTC instant for IST midnight of the calendar day (in IST)
 * containing `instant`.
 */
export function istStartOfDay(instant: Date): Date {
  const shifted = new Date(instant.getTime() + IST_OFFSET_MS)
  const istMidnightAsUtc = Date.UTC(
    shifted.getUTCFullYear(),
    shifted.getUTCMonth(),
    shifted.getUTCDate()
  )
  return new Date(istMidnightAsUtc - IST_OFFSET_MS)
}

/** Returns the last millisecond of the IST calendar day containing
 * `instant` (i.e. one millisecond before the next IST midnight).
 */
export function istEndOfDay(instant: Date): Date {
  return new Date(istStartOfDay(instant).getTime() + DAY_MS - 1)
}

/** Builds the UTC instant for IST midnight of a specific calendar date —
 * used for "Custom Range", where the calendar widget hands back Dates
 * whose local Y/M/D components are the day the user actually clicked;
 * reinterpreting those Y/M/D components (not the instant) as an IST date
 * keeps a click on "25 Aug" meaning IST 25 Aug no matter the browser tz.
 */
export function istMidnightFromLocalParts(date: Date): Date {
  const utcMidnight = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate())
  return new Date(utcMidnight - IST_OFFSET_MS)
}

export function istEndOfDayFromLocalParts(date: Date): Date {
  return new Date(istMidnightFromLocalParts(date).getTime() + DAY_MS - 1)
}

/** Monday-anchored start of the IST week containing `instant`. */
export function istStartOfWeek(instant: Date): Date {
  const shifted = new Date(instant.getTime() + IST_OFFSET_MS)
  const dayOfWeek = shifted.getUTCDay() // 0 = Sunday
  const daysSinceMonday = (dayOfWeek + 6) % 7
  return new Date(istStartOfDay(instant).getTime() - daysSinceMonday * DAY_MS)
}

/** Start of the IST calendar month containing `instant`. */
export function istStartOfMonth(instant: Date): Date {
  const shifted = new Date(instant.getTime() + IST_OFFSET_MS)
  const monthStartAsUtc = Date.UTC(shifted.getUTCFullYear(), shifted.getUTCMonth(), 1)
  return new Date(monthStartAsUtc - IST_OFFSET_MS)
}

/** Returns a browser-local Date whose Y/M/D equal the IST calendar date
 * of `instant` — used only so a calendar/date-picker widget highlights
 * the right day cell and date labels read correctly (both compare/format
 * via local Y/M/D getters), never for arithmetic or for values sent to
 * the API — those always use the UTC instants above.
 */
export function istCalendarDateAsLocalMidnight(instant: Date): Date {
  const shifted = new Date(instant.getTime() + IST_OFFSET_MS)
  return new Date(shifted.getUTCFullYear(), shifted.getUTCMonth(), shifted.getUTCDate())
}

/** True when both instants fall in the same IST calendar day — used to
 * detect "the active range is exactly today" for the Today-vs-Yesterday
 * comparison chart.
 */
export function isSameIstDay(a: Date, b: Date): boolean {
  return istStartOfDay(a).getTime() === istStartOfDay(b).getTime()
}

/** The hour-of-day (0-23) `instant` falls in, read off the IST wall
 * clock — used to align "today" and "yesterday" hourly buckets that
 * otherwise carry different calendar dates.
 */
export function istHourOfDay(instant: Date): number {
  return new Date(instant.getTime() + IST_OFFSET_MS).getUTCHours()
}

export type DashboardDateRangePreset =
  | "today"
  | "yesterday"
  | "this_week"
  | "last_7_days"
  | "last_30_days"
  | "this_month"

/** Resolves the "Default dashboard date range" setting (Administration ->
 * Settings -> Dashboard) to a concrete IST-bounded range as of `now` --
 * the same preset definitions `components/shared/date-range-picker.tsx`
 * uses, kept here so both have exactly one source of truth for what
 * "Today"/"This Week"/etc. mean.
 */
export function resolveDashboardDateRangePreset(
  preset: DashboardDateRangePreset,
  now: Date
): { from: Date; to: Date } {
  switch (preset) {
    case "today":
      return { from: istStartOfDay(now), to: istEndOfDay(now) }
    case "yesterday": {
      const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000)
      return { from: istStartOfDay(yesterday), to: istEndOfDay(yesterday) }
    }
    case "this_week":
      return { from: istStartOfWeek(now), to: istEndOfDay(now) }
    case "last_7_days":
      return {
        from: istStartOfDay(new Date(now.getTime() - 6 * 24 * 60 * 60 * 1000)),
        to: istEndOfDay(now),
      }
    case "this_month":
      return { from: istStartOfMonth(now), to: istEndOfDay(now) }
    case "last_30_days":
    default:
      return {
        from: istStartOfDay(new Date(now.getTime() - 29 * 24 * 60 * 60 * 1000)),
        to: istEndOfDay(now),
      }
  }
}
