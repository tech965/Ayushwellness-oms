"use client"

import * as React from "react"
import { CalendarIcon } from "lucide-react"
import { format } from "date-fns"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"
import {
  istCalendarDateAsLocalMidnight,
  istEndOfDay,
  istEndOfDayFromLocalParts,
  istMidnightFromLocalParts,
  istStartOfDay,
  istStartOfMonth,
  istStartOfWeek,
} from "@/lib/ist-date"

export interface DateRangeValue {
  from?: Date
  to?: Date
}

interface Preset {
  label: string
  range: () => DateRangeValue
}

// Every preset below resolves against the IST calendar day/week/month
// containing "now" (see `lib/ist-date.ts`) rather than the browser's
// local timezone — the OMS's business calendar is IST regardless of
// which timezone the viewer's machine happens to be set to, matching
// `app/core/timezone.py`'s convention on the backend. "This Month"/"This
// Week" resolve to-date (start of period -> end of today), matching the
// existing "Last N Days" presets' own to-date shape.
const PRESETS: Preset[] = [
  {
    label: "Today",
    range: () => {
      const now = new Date()
      return { from: istStartOfDay(now), to: istEndOfDay(now) }
    },
  },
  {
    label: "Yesterday",
    range: () => {
      const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000)
      return { from: istStartOfDay(yesterday), to: istEndOfDay(yesterday) }
    },
  },
  {
    label: "This Week",
    range: () => {
      const now = new Date()
      return { from: istStartOfWeek(now), to: istEndOfDay(now) }
    },
  },
  {
    label: "Last 7 Days",
    range: () => {
      const now = new Date()
      return {
        from: istStartOfDay(new Date(now.getTime() - 6 * 24 * 60 * 60 * 1000)),
        to: istEndOfDay(now),
      }
    },
  },
  {
    label: "Last 30 Days",
    range: () => {
      const now = new Date()
      return {
        from: istStartOfDay(new Date(now.getTime() - 29 * 24 * 60 * 60 * 1000)),
        to: istEndOfDay(now),
      }
    },
  },
  {
    label: "This Month",
    range: () => {
      const now = new Date()
      return { from: istStartOfMonth(now), to: istEndOfDay(now) }
    },
  },
  {
    label: "Last Month",
    range: () => {
      const now = new Date()
      const firstOfThisMonth = istStartOfMonth(now)
      const lastMonthInstant = new Date(firstOfThisMonth.getTime() - 1)
      return {
        from: istStartOfMonth(lastMonthInstant),
        to: istEndOfDay(new Date(firstOfThisMonth.getTime() - 1)),
      }
    },
  },
]

interface DateRangePickerProps {
  value: DateRangeValue
  onChange: (range: DateRangeValue) => void
  className?: string
}

/** Timestamps drift by at most a couple of seconds between when a preset
 * was clicked (`new Date()` at click time) and when it's compared back on
 * render, so treat "same day, same clock-second bucket" as a match rather
 * than requiring exact millisecond equality.
 */
function sameInstant(a: Date | undefined, b: Date | undefined): boolean {
  if (!a || !b) return a === b
  return Math.abs(a.getTime() - b.getTime()) < 2000
}

/** The calendar widget works in browser-local Y/M/D, so the applied IST
 * instants are re-expressed as local midnights for it (see
 * `istCalendarDateAsLocalMidnight`).
 */
function toCalendarRange(value: DateRangeValue): DayRange {
  return {
    from: value.from ? istCalendarDateAsLocalMidnight(value.from) : undefined,
    to: value.to ? istCalendarDateAsLocalMidnight(value.to) : undefined,
  }
}

interface DayRange {
  from?: Date
  to?: Date
}

function formatDay(date: Date): string {
  return format(date, "d MMM yyyy")
}

/** Global date-range selector with the presets every date-scoped screen
 * needs: a horizontal preset toolbar where the active range is
 * filled/highlighted, plus a "Custom Range" popover calendar.
 *
 * The custom calendar edits a local *draft*: picking the start date never
 * propagates to the page (which would refetch and -- on pages that swap in
 * a loading skeleton -- unmount the picker) and never closes the popover.
 * The range is applied to the page only on "Apply" (or a preset click);
 * "Clear" removes the filter. An end date earlier than the start date is
 * impossible: the calendar re-anchors the range on the new earliest day.
 * Everything applied is IST-bounded (`lib/ist-date.ts`).
 */
const WIDE_QUERY = "(min-width: 640px)"

// `matchMedia` is missing in some environments (e.g. jsdom): assume wide.
function hasMatchMedia(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
}

function subscribeToWidth(callback: () => void) {
  if (!hasMatchMedia()) return () => {}
  const query = window.matchMedia(WIDE_QUERY)
  query.addEventListener("change", callback)
  return () => query.removeEventListener("change", callback)
}

export function DateRangePicker({ value, onChange, className }: DateRangePickerProps) {
  // Two side-by-side months only where they fit; one month on phones so
  // the popover (and its Apply/Clear footer) stays inside the viewport.
  const wide = React.useSyncExternalStore(
    subscribeToWidth,
    () => (hasMatchMedia() ? window.matchMedia(WIDE_QUERY).matches : true),
    () => true
  )
  const [open, setOpen] = React.useState(false)
  const [draft, setDraft] = React.useState<DayRange>({})

  const activePreset = PRESETS.find((preset) => {
    const range = preset.range()
    return sameInstant(range.from, value.from) && sameInstant(range.to, value.to)
  })

  // Formatted off the IST calendar date (not the raw UTC instant) so the
  // label reads correctly regardless of the viewer's browser timezone.
  const applied = toCalendarRange(value)
  const customLabel =
    !activePreset && applied.from && applied.to
      ? `${formatDay(applied.from)} – ${formatDay(applied.to)}`
      : "Custom Range"

  function handleOpenChange(next: boolean) {
    // Re-seed the draft from the applied value every time the popover
    // opens, so an abandoned selection never leaks into the next one.
    if (next) setDraft(toCalendarRange(value))
    setOpen(next)
  }

  function apply() {
    if (!draft.from) return
    const last = draft.to ?? draft.from
    onChange({
      from: istMidnightFromLocalParts(draft.from),
      to: istEndOfDayFromLocalParts(last),
    })
    setOpen(false)
  }

  function clear() {
    setDraft({})
    onChange({ from: undefined, to: undefined })
    setOpen(false)
  }

  const summary = !draft.from
    ? "Select a start date"
    : !draft.to
      ? `${formatDay(draft.from)} – select an end date`
      : `${formatDay(draft.from)} – ${formatDay(draft.to)}`

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {PRESETS.map((preset) => (
        <Button
          key={preset.label}
          variant={preset.label === activePreset?.label ? "default" : "outline"}
          size="sm"
          onClick={() => onChange(preset.range())}
          aria-pressed={preset.label === activePreset?.label}
        >
          {preset.label}
        </Button>
      ))}
      <Popover open={open} onOpenChange={handleOpenChange}>
        <PopoverTrigger asChild>
          <Button
            variant={!activePreset && value.from && value.to ? "default" : "outline"}
            size="sm"
            data-testid="date-range-trigger"
            className="gap-1.5 font-normal"
            aria-pressed={!activePreset && Boolean(value.from && value.to)}
          >
            <CalendarIcon className="size-3.5" />
            {customLabel}
          </Button>
        </PopoverTrigger>
        <PopoverContent
          className="max-h-[80dvh] w-auto max-w-[calc(100vw-1rem)] overflow-auto p-0"
          align="end"
        >
          <Calendar
            mode="range"
            defaultMonth={draft.from ?? applied.from}
            selected={draft.from ? { from: draft.from, to: draft.to } : undefined}
            onSelect={(range) => setDraft({ from: range?.from, to: range?.to })}
            numberOfMonths={wide ? 2 : 1}
          />
          <div className="bg-popover sticky bottom-0 flex flex-wrap items-center justify-between gap-2 border-t p-2">
            <span
              className="text-muted-foreground text-xs"
              data-testid="date-range-summary"
            >
              {summary}
            </span>
            <div className="flex gap-1.5">
              <Button
                variant="ghost"
                size="sm"
                onClick={clear}
                data-testid="date-range-clear"
              >
                Clear
              </Button>
              <Button
                size="sm"
                onClick={apply}
                disabled={!draft.from}
                data-testid="date-range-apply"
              >
                Apply
              </Button>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}
