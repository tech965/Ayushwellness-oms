"use client"

import * as React from "react"
import { CalendarIcon } from "lucide-react"
import {
  endOfMonth,
  format,
  startOfDay,
  startOfMonth,
  subDays,
  subMonths,
} from "date-fns"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { cn } from "@/lib/utils"

export interface DateRangeValue {
  from?: Date
  to?: Date
}

interface Preset {
  label: string
  range: () => DateRangeValue
}

function endOfToday(): Date {
  const now = new Date()
  now.setHours(23, 59, 59, 999)
  return now
}

const PRESETS: Preset[] = [
  { label: "Today", range: () => ({ from: startOfDay(new Date()), to: endOfToday() }) },
  {
    label: "Yesterday",
    range: () => {
      const yesterday = subDays(new Date(), 1)
      const end = new Date(yesterday)
      end.setHours(23, 59, 59, 999)
      return { from: startOfDay(yesterday), to: end }
    },
  },
  {
    label: "Last 7 Days",
    range: () => ({ from: startOfDay(subDays(new Date(), 6)), to: endOfToday() }),
  },
  {
    label: "Last 30 Days",
    range: () => ({ from: startOfDay(subDays(new Date(), 29)), to: endOfToday() }),
  },
  {
    label: "This Month",
    range: () => ({ from: startOfMonth(new Date()), to: endOfToday() }),
  },
  {
    label: "Last Month",
    range: () => {
      const lastMonth = subMonths(new Date(), 1)
      return { from: startOfMonth(lastMonth), to: endOfMonth(lastMonth) }
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

  const customLabel =
    !activePreset && value.from && value.to
      ? `${format(value.from, "d MMM yyyy")} – ${format(value.to, "d MMM yyyy")}`
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
            selected={{ from: value.from, to: value.to }}
            onSelect={(range) => {
              onChange({ from: range?.from, to: range?.to })
              if (range?.from && range?.to) setOpen(false)
            }}
            numberOfMonths={2}
          />
        </PopoverContent>
      </Popover>
    </div>
  )
}
