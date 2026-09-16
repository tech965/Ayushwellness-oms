"use client"

import * as React from "react"
import { CalendarIcon } from "lucide-react"
import { format } from "date-fns"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  istCalendarDateAsLocalMidnight,
  istMidnightFromLocalParts,
  istStartOfDay,
} from "@/lib/ist-date"
import { cn } from "@/lib/utils"

interface StockDatePickerProps {
  /** A UTC instant that IS IST midnight of the selected calendar date
   * (i.e. already normalized via `istStartOfDay`/`istMidnightFromLocalParts`)
   * — never a raw "now". */
  value: Date
  onChange: (date: Date) => void
  className?: string
}

/** Single-date selector for the multi-platform inventory page's "Stock
 * Date" filter — Today / Yesterday / any previous date, always resolved
 * against the IST calendar day (never the browser's local timezone),
 * mirroring `DateRangePicker`'s existing preset-toolbar-plus-popover-
 * calendar pattern trimmed to a single date instead of a range (there is
 * no existing single-date component to reuse verbatim — this is the
 * closest fit to the established convention).
 */
export function StockDatePicker({ value, onChange, className }: StockDatePickerProps) {
  const [open, setOpen] = React.useState(false)

  const today = istStartOfDay(new Date())
  const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000)
  const isToday = value.getTime() === today.getTime()
  const isYesterday = value.getTime() === yesterday.getTime()
  const isCustom = !isToday && !isYesterday

  // The custom-date button's own label must never repeat "Today"/
  // "Yesterday" (those already have their own buttons, immediately to
  // its left) -- it shows the actual date only while that date is the
  // active selection, else a neutral placeholder, mirroring
  // `DateRangePicker`'s "Custom Range" placeholder-when-inactive
  // convention exactly.
  const customLabel = isCustom
    ? format(istCalendarDateAsLocalMidnight(value), "d MMM yyyy")
    : "Custom Date"

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      <span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        Stock Date
      </span>
      <Button
        variant={isToday ? "default" : "outline"}
        size="sm"
        onClick={() => onChange(today)}
        aria-pressed={isToday}
      >
        Today
      </Button>
      <Button
        variant={isYesterday ? "default" : "outline"}
        size="sm"
        onClick={() => onChange(yesterday)}
        aria-pressed={isYesterday}
      >
        Yesterday
      </Button>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant={isCustom ? "default" : "outline"}
            size="sm"
            className="gap-1.5 font-normal"
            aria-pressed={isCustom}
          >
            <CalendarIcon className="size-3.5" />
            {customLabel}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <Calendar
            mode="single"
            selected={istCalendarDateAsLocalMidnight(value)}
            onSelect={(date) => {
              if (!date) return
              onChange(istMidnightFromLocalParts(date))
              setOpen(false)
            }}
            disabled={(date) => istMidnightFromLocalParts(date).getTime() > today.getTime()}
          />
        </PopoverContent>
      </Popover>
    </div>
  )
}
