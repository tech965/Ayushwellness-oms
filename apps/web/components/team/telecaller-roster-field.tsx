"use client"

import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAssignableTelecallers } from "@/services/team"

interface TelecallerRosterFieldProps {
  mode: "manual" | "equal"
  manualValue: string
  onManualChange: (id: string) => void
  selectedIds: Set<string>
  onToggle: (id: string) => void
}

/** The "Select Telecaller" part of every Bulk Assign dialog (Lead Pool,
 * Abandoned Checkouts, Unfulfilled Orders) — one shared component so the
 * three pages can never drift on how the roster is fetched or how its
 * loading/error/empty states are handled. Renders a single-select
 * dropdown for `mode="manual"`, or a checkbox list for `mode="equal"`,
 * against `useAssignableTelecallers()` (every active TELECALLER-role user
 * in scope, whether or not they've ever had a lead assigned).
 */
export function TelecallerRosterField({
  mode,
  manualValue,
  onManualChange,
  selectedIds,
  onToggle,
}: TelecallerRosterFieldProps) {
  const query = useAssignableTelecallers()

  if (query.isLoading) {
    return <p className="text-muted-foreground text-sm">Loading telecallers...</p>
  }

  if (query.isError) {
    return (
      <div className="border-destructive/40 bg-destructive/5 flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm">
        <span className="text-destructive">Could not load telecallers.</span>
        <button
          type="button"
          className="text-primary text-sm font-medium hover:underline"
          onClick={() => void query.refetch()}
        >
          Retry
        </button>
      </div>
    )
  }

  const telecallers = query.data ?? []

  if (telecallers.length === 0) {
    return <p className="text-muted-foreground text-sm">No active telecallers found.</p>
  }

  if (mode === "manual") {
    return (
      <Select value={manualValue} onValueChange={onManualChange}>
        <SelectTrigger>
          <SelectValue placeholder="Select telecaller" />
        </SelectTrigger>
        <SelectContent>
          {telecallers.map((t) => (
            <SelectItem key={t.id} value={t.id}>
              {t.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        Distribute equally across
      </p>
      {telecallers.map((t) => (
        <label key={t.id} className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={selectedIds.has(t.id)}
            onCheckedChange={() => onToggle(t.id)}
          />
          {t.name}
        </label>
      ))}
    </div>
  )
}
