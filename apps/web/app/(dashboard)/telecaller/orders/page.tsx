"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import {
  CalendarClock,
  CheckCircle2,
  History,
  MoreVertical,
  PackageCheck,
  PhoneCall,
} from "lucide-react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDate, formatDateTime, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import {
  useBulkConfirmOrders,
  useCallHistory,
  useConfirmOrder,
  useLogCall,
  useMyOrders,
  useScheduleFollowUp,
} from "@/services/telecaller"
import {
  CALL_OUTCOME_OPTIONS,
  type AssignedOrder,
  type TelecallingStatus,
} from "@/types/telecalling"

const FILTER_DEFAULTS = { call_status: "", page: 1, page_size: 20 }

const CALL_STATUS_OPTIONS = [
  { value: "not_called", label: "Not Called" },
  ...CALL_OUTCOME_OPTIONS,
]

export default function TelecallerOrdersPage() {
  return (
    <React.Suspense>
      <TelecallerOrdersContent />
    </React.Suspense>
  )
}

function TelecallerOrdersContent() {
  const router = useRouter()
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)
  const query = useMyOrders({
    page: filters.page,
    pageSize: filters.page_size,
    call_status: filters.call_status || undefined,
  })

  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(new Set())
  const [bulkConfirmOpen, setBulkConfirmOpen] = React.useState(false)
  const bulkConfirm = useBulkConfirmOrders()

  const [logCallTarget, setLogCallTarget] = React.useState<AssignedOrder | null>(null)
  const [outcome, setOutcome] = React.useState<TelecallingStatus>("connected")
  const [notes, setNotes] = React.useState("")
  const logCall = useLogCall(logCallTarget?.order_id ?? "")

  const [followUpTarget, setFollowUpTarget] = React.useState<AssignedOrder | null>(null)
  const [followUpValue, setFollowUpValue] = React.useState("")
  const scheduleFollowUp = useScheduleFollowUp(followUpTarget?.order_id ?? "")

  const [historyTarget, setHistoryTarget] = React.useState<AssignedOrder | null>(null)
  const historyQuery = useCallHistory(historyTarget?.order_id ?? "")

  const [confirmTarget, setConfirmTarget] = React.useState<AssignedOrder | null>(null)
  const confirmOrder = useConfirmOrder(confirmTarget?.order_id ?? "")

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

  function handleLogCall() {
    if (!logCallTarget) return
    logCall.mutate(
      { outcome, notes: notes || undefined },
      {
        onSuccess: () => {
          toast.success("Call status updated.")
          setLogCallTarget(null)
          setNotes("")
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  function handleScheduleFollowUp() {
    if (!followUpTarget || !followUpValue) return
    scheduleFollowUp.mutate(new Date(followUpValue).toISOString(), {
      onSuccess: () => {
        toast.success("Follow-up scheduled.")
        setFollowUpTarget(null)
        setFollowUpValue("")
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  function handleBulkConfirm() {
    bulkConfirm.mutate(Array.from(selectedIds), {
      onSuccess: (result) => {
        if (result.failed_count === 0) {
          toast.success(`${result.confirmed_count} orders confirmed successfully.`)
        } else {
          toast.warning(
            `${result.confirmed_count} confirmed, ${result.failed_count} could not be confirmed.`
          )
        }
        setBulkConfirmOpen(false)
        setSelectedIds(new Set())
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  function handleConfirmOrder() {
    if (!confirmTarget) return
    confirmOrder.mutate(undefined, {
      onSuccess: () => {
        toast.success("Order confirmed — ready for shipment.")
        setConfirmTarget(null)
      },
      onError: (error) => {
        toast.error(getApiErrorMessage(error))
        setConfirmTarget(null)
      },
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
      id: "item_summary",
      header: "Product",
      cell: (o) => (
        <span className="block max-w-[200px] truncate">{o.item_summary ?? "—"}</span>
      ),
    },
    {
      id: "total_amount",
      header: "Amount",
      className: "text-right",
      cell: (o) => formatMoney(o.total_amount),
    },
    {
      id: "payment_type",
      header: "Payment",
      cell: (o) => <StatusBadge domain="payment" status={o.payment_type} />,
    },
    {
      id: "call_status",
      header: "Call Status",
      cell: (o) => (
        <StatusBadge domain="telecalling" status={o.call_status ?? "not_called"} />
      ),
    },
    {
      id: "status",
      header: "Order Status",
      cell: (o) => <StatusBadge domain="order" status={o.status} />,
    },
    {
      id: "attempt_count",
      header: "Attempts",
      className: "text-right",
      cell: (o) => o.attempt_count,
    },
    {
      id: "next_follow_up_at",
      header: "Next Follow-up",
      cell: (o) => (o.next_follow_up_at ? formatDate(o.next_follow_up_at) : "—"),
    },
    {
      id: "actions",
      header: "",
      className: "w-10",
      cell: (o) => (
        <div onClick={(e) => e.stopPropagation()}>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Order actions">
                <MoreVertical />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                onSelect={() => {
                  setLogCallTarget(o)
                  setOutcome((o.call_status ?? "connected") as TelecallingStatus)
                  setNotes("")
                }}
              >
                <PhoneCall />
                Edit Call Status
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => {
                  setFollowUpTarget(o)
                  setFollowUpValue("")
                }}
              >
                <CalendarClock />
                Edit Follow-up
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setHistoryTarget(o)}>
                <History />
                View Call History
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => router.push(`/telecaller/orders/${o.order_id}`)}>
                View Order
              </DropdownMenuItem>
              {o.status === "pending" && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={() => setConfirmTarget(o)}>
                    <PackageCheck />
                    Confirm Order
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="My Assigned Orders"
        description={
          query.data
            ? `${query.data.meta.total_items} orders assigned to you.`
            : "Only orders assigned to you appear here."
        }
        actions={
          selectedIds.size > 0 ? (
            <Button size="sm" onClick={() => setBulkConfirmOpen(true)}>
              <CheckCircle2 />
              Bulk Confirm ({selectedIds.size})
            </Button>
          ) : undefined
        }
      />
      <div className="flex flex-col gap-4">
        <FilterBar
          extra={
            <Select
              value={filters.call_status || "__all__"}
              onValueChange={(v) => setFilters({ call_status: v === "__all__" ? "" : v })}
            >
              <SelectTrigger className="w-[180px]">
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
          }
        />

        {selectedIds.size > 0 && (
          <div className="bg-muted/50 border-border flex items-center justify-between rounded-lg border px-4 py-2 text-sm">
            <span className="font-medium">{selectedIds.size} orders selected</span>
            <Button variant="ghost" size="sm" onClick={() => setSelectedIds(new Set())}>
              Clear selection
            </Button>
          </div>
        )}

        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No orders assigned"
          emptyDescription="Your team leader hasn't assigned you any orders yet."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(o) => o.order_id}
                onRowClick={(o) => router.push(`/telecaller/orders/${o.order_id}`)}
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

      <Dialog
        open={logCallTarget !== null}
        onOpenChange={(open) => !open && setLogCallTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Edit Call Status {logCallTarget ? `— ${logCallTarget.order_number}` : ""}
            </DialogTitle>
            <DialogDescription>Log a call outcome without opening the order.</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <Select value={outcome} onValueChange={(v) => setOutcome(v as TelecallingStatus)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CALL_OUTCOME_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Textarea
              placeholder="Notes (optional)"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLogCallTarget(null)}>
              Cancel
            </Button>
            <Button onClick={handleLogCall} disabled={logCall.isPending}>
              {logCall.isPending ? "Saving..." : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={followUpTarget !== null}
        onOpenChange={(open) => !open && setFollowUpTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Edit Follow-up {followUpTarget ? `— ${followUpTarget.order_number}` : ""}
            </DialogTitle>
            <DialogDescription>Set the next follow-up date/time.</DialogDescription>
          </DialogHeader>
          <Input
            type="datetime-local"
            value={followUpValue}
            onChange={(e) => setFollowUpValue(e.target.value)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setFollowUpTarget(null)}>
              Cancel
            </Button>
            <Button
              onClick={handleScheduleFollowUp}
              disabled={scheduleFollowUp.isPending || !followUpValue}
            >
              {scheduleFollowUp.isPending ? "Saving..." : "Schedule"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={historyTarget !== null}
        onOpenChange={(open) => !open && setHistoryTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Call History {historyTarget ? `— ${historyTarget.order_number}` : ""}
            </DialogTitle>
            <DialogDescription>Read-only — open the order to edit an attempt.</DialogDescription>
          </DialogHeader>
          <QueryStates
            isLoading={historyQuery.isLoading}
            isError={historyQuery.isError}
            error={historyQuery.error}
            data={historyQuery.data}
            onRetry={() => void historyQuery.refetch()}
            isEmpty={(events) => events.length === 0}
            emptyTitle="No calls logged yet"
          >
            {(attempts) => (
              <ol className="flex max-h-80 flex-col gap-3 overflow-y-auto">
                {attempts.map((attempt) => (
                  <li
                    key={attempt.id}
                    className="border-border border-b pb-3 last:border-0 last:pb-0"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">
                        Attempt #{attempt.attempt_number}
                      </span>
                      <StatusBadge domain="telecalling" status={attempt.outcome} />
                    </div>
                    <p className="text-muted-foreground mt-0.5 text-xs">
                      {formatDateTime(attempt.attempted_at)}
                    </p>
                    {attempt.notes && <p className="mt-1 text-sm">{attempt.notes}</p>}
                  </li>
                ))}
              </ol>
            )}
          </QueryStates>
        </DialogContent>
      </Dialog>

      <AlertDialog open={bulkConfirmOpen} onOpenChange={setBulkConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm {selectedIds.size} selected orders?</AlertDialogTitle>
            <AlertDialogDescription>
              This marks each order ready for shipment. It does not create a shipment or change
              call status.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button onClick={handleBulkConfirm} disabled={bulkConfirm.isPending}>
              {bulkConfirm.isPending ? "Confirming..." : "Confirm Orders"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={confirmTarget !== null}
        onOpenChange={(open) => !open && setConfirmTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Confirm order {confirmTarget ? confirmTarget.order_number : ""}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This marks the order ready for shipment. It does not create a shipment and does
              not change the call status.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button onClick={handleConfirmOrder} disabled={confirmOrder.isPending}>
              {confirmOrder.isPending ? "Confirming..." : "Confirm Order"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
