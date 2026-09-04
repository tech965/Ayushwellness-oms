"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import {
  DateRangePicker,
  type DateRangeValue,
} from "@/components/shared/date-range-picker"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { TelecallerRosterField } from "@/components/team/telecaller-roster-field"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useAssignCheckouts, useTeamCheckouts } from "@/services/team"
import { CALL_OUTCOME_OPTIONS, type AssignedCheckout } from "@/types/telecalling"

const FILTER_DEFAULTS = {
  call_status: "",
  date_from: "",
  date_to: "",
  page: 1,
  page_size: 20,
}

const CALL_STATUS_OPTIONS = [
  { value: "not_called", label: "Not Called" },
  ...CALL_OUTCOME_OPTIONS,
]

export default function TeamCheckoutsPage() {
  return (
    <React.Suspense>
      <TeamCheckoutsContent />
    </React.Suspense>
  )
}

function TeamCheckoutsContent() {
  const router = useRouter()
  const { filters, setFilters, clearFilters } = useUrlFilters(FILTER_DEFAULTS)
  const assignMutation = useAssignCheckouts()

  const dateRange: DateRangeValue = {
    from: filters.date_from ? new Date(filters.date_from) : undefined,
    to: filters.date_to ? new Date(filters.date_to) : undefined,
  }

  const query = useTeamCheckouts({
    page: filters.page,
    pageSize: filters.page_size,
    call_status: filters.call_status || undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  })

  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(new Set())
  const [assignOpen, setAssignOpen] = React.useState(false)
  const [mode, setMode] = React.useState<"manual" | "equal">("equal")
  const [manualTelecallerId, setManualTelecallerId] = React.useState("")
  const [equalTelecallerIds, setEqualTelecallerIds] = React.useState<Set<string>>(
    new Set()
  )

  function toggleOne(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAllOnPage(ids: string[]) {
    setSelectedIds((prev) => {
      const allSelected = ids.every((id) => prev.has(id))
      const next = new Set(prev)
      for (const id of ids) {
        if (allSelected) next.delete(id)
        else next.add(id)
      }
      return next
    })
  }

  function toggleEqualTelecaller(id: string) {
    setEqualTelecallerIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function handleAssign() {
    const checkout_ids = Array.from(selectedIds)
    if (checkout_ids.length === 0) return

    const payload =
      mode === "manual"
        ? { checkout_ids, mode: "manual" as const, telecaller_id: manualTelecallerId }
        : {
            checkout_ids,
            mode: "equal" as const,
            telecaller_ids: Array.from(equalTelecallerIds),
          }

    assignMutation.mutate(payload, {
      onSuccess: (assignments) => {
        toast.success(`${assignments.length} checkout(s) assigned.`)
        setAssignOpen(false)
        setSelectedIds(new Set())
        setManualTelecallerId("")
        setEqualTelecallerIds(new Set())
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  const columns: DataTableColumn<AssignedCheckout>[] = [
    { id: "customer_name", header: "Customer", cell: (c) => c.customer_name ?? "—" },
    { id: "customer_phone", header: "Phone", cell: (c) => c.customer_phone ?? "—" },
    { id: "customer_email", header: "Email", cell: (c) => c.customer_email ?? "—" },
    {
      id: "item_summary",
      header: "Product",
      cell: (c) => (
        <span className="block max-w-[200px] truncate">{c.item_summary ?? "—"}</span>
      ),
    },
    {
      id: "total_amount",
      header: "Cart Amount",
      className: "text-right",
      cell: (c) => formatMoney(c.total_amount),
    },
    {
      id: "checkout_created_at",
      header: "Checkout Date",
      cell: (c) => (c.checkout_created_at ? formatDateTime(c.checkout_created_at) : "—"),
    },
    {
      id: "priority",
      header: "Priority",
      cell: (c) =>
        c.priority ? <StatusBadge domain="lead_priority" status={c.priority} /> : "—",
    },
    {
      id: "assigned_to_name",
      header: "Assigned Telecaller",
      cell: (c) =>
        c.assigned_to_name ?? <span className="text-muted-foreground">Unassigned</span>,
    },
    {
      id: "call_status",
      header: "Call Status",
      cell: (c) =>
        c.assignment_id ? (
          <StatusBadge domain="telecalling" status={c.call_status ?? "not_called"} />
        ) : (
          "—"
        ),
    },
  ]

  const canAssign = selectedIds.size > 0

  return (
    <>
      <PageHeader
        title="Abandoned Checkouts"
        description={
          query.data
            ? `${query.data.meta.total_items} recovery leads from Shopify — real checkout data only, never fabricated.`
            : "Customers who started checkout but never completed the purchase."
        }
        actions={
          <Button size="sm" disabled={!canAssign} onClick={() => setAssignOpen(true)}>
            Bulk Assign{canAssign ? ` (${selectedIds.size})` : ""}
          </Button>
        }
      />

      <div className="flex flex-col gap-4">
        <div className="bg-card border-border flex flex-wrap items-center gap-2 rounded-lg border p-3">
          <DateRangePicker
            value={dateRange}
            onChange={(range) =>
              setFilters({
                date_from: range.from ? range.from.toISOString() : "",
                date_to: range.to ? range.to.toISOString() : "",
              })
            }
          />
          <Select
            value={filters.call_status || "__all__"}
            onValueChange={(v) => setFilters({ call_status: v === "__all__" ? "" : v })}
          >
            <SelectTrigger className="w-[170px]">
              <SelectValue placeholder="Call Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All call statuses</SelectItem>
              {CALL_STATUS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="ghost" size="sm" onClick={() => clearFilters()}>
            Clear
          </Button>
        </div>

        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No abandoned checkouts"
          emptyDescription="Nothing open and contactable right now — check back after the next Shopify sync."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(c) => c.checkout_id}
                onRowClick={(c) => router.push(`/team/checkouts/${c.checkout_id}`)}
                selection={{
                  selectedIds,
                  onToggle: toggleOne,
                  onToggleAll: toggleAllOnPage,
                }}
              />
              <PaginationBar
                meta={data.meta}
                onPageChange={(page) => setFilters({ page })}
                pageSizeOptions={[10, 20, 50, 100]}
                onPageSizeChange={(page_size) => setFilters({ page_size, page: 1 })}
              />
            </>
          )}
        </QueryStates>
      </div>

      <Dialog open={assignOpen} onOpenChange={setAssignOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Bulk Assign {selectedIds.size} Checkout(s)</DialogTitle>
            <DialogDescription>
              A checkout that&apos;s already actively assigned is skipped by this action —
              use Reassign from the checkout detail page for those instead.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-4">
            <Select value={mode} onValueChange={(v) => setMode(v as "manual" | "equal")}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="equal">Equal distribution</SelectItem>
                <SelectItem value="manual">Manual (one telecaller)</SelectItem>
              </SelectContent>
            </Select>

            <TelecallerRosterField
              mode={mode}
              manualValue={manualTelecallerId}
              onManualChange={setManualTelecallerId}
              selectedIds={equalTelecallerIds}
              onToggle={toggleEqualTelecaller}
            />
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setAssignOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleAssign}
              disabled={
                assignMutation.isPending ||
                (mode === "manual" ? !manualTelecallerId : equalTelecallerIds.size === 0)
              }
            >
              {assignMutation.isPending ? "Assigning..." : "Assign"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
