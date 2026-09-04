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

/** Global date-range selector with the presets every date-scoped screen
 * needs (spec: dashboard + orders): a horizontal preset toolbar where the
 * active range is filled/highlighted, plus a "Custom Range" popover
 * calendar for anything else. Generalizes the inline picker in `FilterBar`
 * into a standalone, reusable component.
 */
export function DateRangePicker({ value, onChange, className }: DateRangePickerProps) {
  const [open, setOpen] = React.useState(false)

  const activePreset = PRESETS.find((preset) => {
    const range = preset.range()
    return sameInstant(range.from, value.from) && sameInstant(range.to, value.to)
  })

  // Formatted off the IST calendar date (not the raw UTC instant) so the
  // label reads correctly regardless of the viewer's browser timezone.
  const customLabel =
    !activePreset && value.from && value.to
      ? `${format(istCalendarDateAsLocalMidnight(value.from), "d MMM yyyy")} – ${format(istCalendarDateAsLocalMidnight(value.to), "d MMM yyyy")}`
      : "Custom Range"

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
      <Popover open={open} onOpenChange={setOpen}>
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
        <PopoverContent className="w-auto p-0" align="end">
          <Calendar
            mode="range"
            // The calendar widget compares/highlights day cells via local
            // Y/M/D getters, so it's fed the IST calendar date re-expressed
            // as a local midnight — not the raw UTC instant `value` holds
            // (see `istCalendarDateAsLocalMidnight`'s docstring).
            selected={{
              from: value.from ? istCalendarDateAsLocalMidnight(value.from) : undefined,
              to: value.to ? istCalendarDateAsLocalMidnight(value.to) : undefined,
            }}
            onSelect={(range) => {
              onChange({
                from: range?.from ? istMidnightFromLocalParts(range.from) : undefined,
                to: range?.to ? istEndOfDayFromLocalParts(range.to) : undefined,
              })
              if (range?.from && range?.to) setOpen(false)
            }}
            numberOfMonths={2}
          />
        </PopoverContent>
      </Popover>
    </div>
  )
}
