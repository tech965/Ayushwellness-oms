"use client"

import * as React from "react"
import { CalendarIcon, Search, X } from "lucide-react"
import { format } from "date-fns"

import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Input } from "@/components/ui/input"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

const ALL_VALUE = "__all__"

export interface FilterOption {
  label: string
  value: string
}

export interface DateRangeValue {
  from?: Date
  to?: Date
}

interface FilterBarProps {
  searchValue?: string
  onSearchChange?: (value: string) => void
  searchPlaceholder?: string
  statusValue?: string
  onStatusChange?: (value: string | undefined) => void
  statusOptions?: FilterOption[]
  statusLabel?: string
  dateRange?: DateRangeValue
  onDateRangeChange?: (range: DateRangeValue) => void
  extra?: React.ReactNode
  className?: string
}

/** Composable search + status + date-range filter row, shared across
 * every list page instead of each one hand-rolling its own (spec §37/§42-47).
 */
export function FilterBar({
  searchValue,
  onSearchChange,
  searchPlaceholder = "Search...",
  statusValue,
  onStatusChange,
  statusOptions,
  statusLabel = "Status",
  dateRange,
  onDateRangeChange,
  extra,
  className,
}: FilterBarProps) {
  const hasActiveFilters = Boolean(
    searchValue || statusValue || dateRange?.from || dateRange?.to
  )

  function clearAll() {
    onSearchChange?.("")
    onStatusChange?.(undefined)
    onDateRangeChange?.({ from: undefined, to: undefined })
  }

  return (
    <div
      className={cn(
        "bg-card border-border flex flex-wrap items-center gap-2 rounded-lg border p-3",
        className
      )}
    >
      {onSearchChange && (
        <div className="relative w-full max-w-xs">
          <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
          <Input
            value={searchValue ?? ""}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder={searchPlaceholder}
            className="pl-8"
          />
        </div>
      )}

      {statusOptions && onStatusChange && (
        <Select
          value={statusValue ?? ALL_VALUE}
          onValueChange={(value) =>
            onStatusChange(value === ALL_VALUE ? undefined : value)
          }
        >
          <SelectTrigger className="w-[170px]">
            <SelectValue placeholder={statusLabel} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_VALUE}>All {statusLabel.toLowerCase()}</SelectItem>
            {statusOptions.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      {onDateRangeChange && (
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              className={cn(
                "gap-2 font-normal",
                !dateRange?.from && !dateRange?.to && "text-muted-foreground"
              )}
            >
              <CalendarIcon className="size-4" />
              {dateRange?.from ? format(dateRange.from, "d MMM") : "From"} &ndash;{" "}
              {dateRange?.to ? format(dateRange.to, "d MMM") : "To"}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0" align="start">
            <Calendar
              mode="range"
              selected={{ from: dateRange?.from, to: dateRange?.to }}
              onSelect={(range) =>
                onDateRangeChange({ from: range?.from, to: range?.to })
              }
              numberOfMonths={2}
            />
          </PopoverContent>
        </Popover>
      )}

      {hasActiveFilters && (
        <Button variant="ghost" size="sm" onClick={clearAll}>
          <X className="size-4" />
          Clear
        </Button>
      )}

      {extra}
    </div>
  )
}
