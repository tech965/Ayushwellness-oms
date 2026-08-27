"use client"

import * as React from "react"
import { CalendarIcon } from "lucide-react"
import {
  endOfMonth,
  format,
  startOfDay,
  startOfMonth,
  startOfYear,
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
  {
    label: "This Year",
    range: () => ({ from: startOfYear(new Date()), to: endOfToday() }),
  },
]

interface DateRangePickerProps {
  value: DateRangeValue
  onChange: (range: DateRangeValue) => void
  className?: string
}

/** Global date-range selector with the presets every date-scoped screen
 * needs (spec: dashboard + orders), plus a custom range calendar.
 * Generalizes the inline picker in `FilterBar` into a standalone,
 * reusable component.
 */
export function DateRangePicker({ value, onChange, className }: DateRangePickerProps) {
  const [open, setOpen] = React.useState(false)

  const label =
    value.from && value.to
      ? `${format(value.from, "d MMM yyyy")} – ${format(value.to, "d MMM yyyy")}`
      : "Select date range"

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          data-testid="date-range-trigger"
          className={cn(
            "gap-2 font-normal",
            !value.from && !value.to && "text-muted-foreground",
            className
          )}
        >
          <CalendarIcon className="size-4" />
          {label}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="flex w-auto gap-0 p-0" align="start">
        <div className="flex flex-col gap-0.5 border-r p-2">
          {PRESETS.map((preset) => (
            <Button
              key={preset.label}
              variant="ghost"
              size="sm"
              className="justify-start font-normal"
              onClick={() => {
                onChange(preset.range())
                setOpen(false)
              }}
            >
              {preset.label}
            </Button>
          ))}
        </div>
        <Calendar
          mode="range"
          selected={{ from: value.from, to: value.to }}
          onSelect={(range) => onChange({ from: range?.from, to: range?.to })}
          numberOfMonths={2}
        />
      </PopoverContent>
    </Popover>
  )
}
