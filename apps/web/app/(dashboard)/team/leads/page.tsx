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
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useAssignOrders, useLeadPool, useTeamTelecallers } from "@/services/team"
import {
  CALL_OUTCOME_OPTIONS,
  LEAD_CATEGORY_OPTIONS,
  type AssignedOrder,
  type LeadCategory,
} from "@/types/telecalling"

const FILTER_DEFAULTS = {
  category: "",
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

export default function LeadPoolPage() {
  return (
    <React.Suspense>
      <LeadPoolContent />
    </React.Suspense>
  )
}

function LeadPoolContent() {
  const router = useRouter()
  const { filters, setFilters, clearFilters } = useUrlFilters(FILTER_DEFAULTS)
  const telecallersQuery = useTeamTelecallers()
  const assignMutation = useAssignOrders()

  const category = (filters.category || undefined) as LeadCategory | undefined

  const dateRange: DateRangeValue = {
    from: filters.date_from ? new Date(filters.date_from) : undefined,
    to: filters.date_to ? new Date(filters.date_to) : undefined,
  }

  const query = useLeadPool({
    page: filters.page,
    pageSize: filters.page_size,
    category,
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
    const order_ids = Array.from(selectedIds)
    if (order_ids.length === 0) return

    const payload =
      mode === "manual"
        ? { order_ids, mode: "manual" as const, telecaller_id: manualTelecallerId }
        : {
            order_ids,
            mode: "equal" as const,
            telecaller_ids: Array.from(equalTelecallerIds),
          }

    assignMutation.mutate(payload, {
      onSuccess: (assignments) => {
        toast.success(`${assignments.length} lead(s) assigned.`)
        setAssignOpen(false)
        setSelectedIds(new Set())
        setManualTelecallerId("")
        setEqualTelecallerIds(new Set())
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  const columns: DataTableColumn<AssignedOrder>[] = [
    {
      id: "order_number",
      header: "Order",
      cell: (o) => <span className="font-medium">{o.order_number}</span>,
    },
    { id: "customer_name", header: "Customer", cell: (o) => o.customer_name ?? "—" },
    { id: "customer_phone", header: "Phone", cell: (o) => o.customer_phone ?? "—" },
    {
      id: "total_amount",
      header: "Amount",
      className: "text-right",
      cell: (o) => formatMoney(o.total_amount),
    },
    { id: "order_datetime", header: "Date", cell: (o) => formatDate(o.order_datetime) },
    {
      id: "lead_category",
      header: "Category",
      cell: (o) =>
        o.lead_category ? (
          <StatusBadge domain="lead_category" status={o.lead_category} />
        ) : (
          "—"
        ),
    },
    {
      id: "priority",
      header: "Priority",
      cell: (o) =>
        o.priority ? <StatusBadge domain="lead_priority" status={o.priority} /> : "—",
    },
    {
      id: "assigned_to_name",
      header: "Assigned Telecaller",
      cell: (o) =>
        o.assigned_to_name ?? <span className="text-muted-foreground">Unassigned</span>,
    },
    {
      id: "call_status",
      header: "Call Status",
      cell: (o) =>
        o.assignment_id ? (
          <StatusBadge domain="telecalling" status={o.call_status ?? "not_called"} />
        ) : (
          "—"
        ),
    },
  ]

  const canAssign = selectedIds.size > 0

  return (
    <>
      <PageHeader
        title="Lead Pool"
        description={
          query.data
            ? `${query.data.meta.total_items} calling leads — COD Unfulfilled, COD Fulfilled, and Prepaid, never mixed.`
            : "Select leads to assign to your telecallers."
        }
        actions={
          <Button size="sm" disabled={!canAssign} onClick={() => setAssignOpen(true)}>
            Bulk Assign{canAssign ? ` (${selectedIds.size})` : ""}
          </Button>
        }
      />

      <div className="flex flex-col gap-4">
        <Tabs
          value={filters.category || "all"}
          onValueChange={(v) => setFilters({ category: v === "all" ? "" : v, page: 1 })}
        >
          <TabsList>
            <TabsTrigger value="all">All</TabsTrigger>
            {LEAD_CATEGORY_OPTIONS.map((o) => (
              <TabsTrigger key={o.value} value={o.value}>
                {o.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

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
          emptyTitle="No leads in this category"
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(o) => o.order_id}
                onRowClick={(o) => router.push(`/team/orders/${o.order_id}`)}
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
            <DialogTitle>Bulk Assign {selectedIds.size} Lead(s)</DialogTitle>
            <DialogDescription>
              An order that&apos;s already actively assigned is skipped by this action —
              use Reassign from the lead detail page for those instead.
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

            {mode === "manual" ? (
              <Select value={manualTelecallerId} onValueChange={setManualTelecallerId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select telecaller" />
                </SelectTrigger>
                <SelectContent>
                  {telecallersQuery.data?.map((t) => (
                    <SelectItem key={t.telecaller_id} value={t.telecaller_id}>
                      {t.telecaller_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <div className="flex flex-col gap-2">
                <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                  Distribute equally across
                </p>
                {telecallersQuery.data?.map((t) => (
                  <label
                    key={t.telecaller_id}
                    className="flex items-center gap-2 text-sm"
                  >
                    <Checkbox
                      checked={equalTelecallerIds.has(t.telecaller_id)}
                      onCheckedChange={() => toggleEqualTelecaller(t.telecaller_id)}
                    />
                    {t.telecaller_name}
                  </label>
                ))}
              </div>
            )}
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
