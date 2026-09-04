"use client"

import * as React from "react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useAdjustStock, useInventoryMovements, useInventoryStock } from "@/services/inventory"
import {
  INVENTORY_MOVEMENT_TYPE_OPTIONS,
  type InventoryMovement,
  type InventoryStock,
} from "@/types/inventory"

function movementTypeLabel(type: InventoryMovement["movement_type"]): string {
  return INVENTORY_MOVEMENT_TYPE_OPTIONS.find((option) => option.value === type)?.label ?? type
}

function AdjustStockAction({ stock }: { stock: InventoryStock }) {
  const { hasPermission } = useAuth()
  const [open, setOpen] = React.useState(false)
  const [delta, setDelta] = React.useState("")
  const [reason, setReason] = React.useState("")
  const adjust = useAdjustStock(stock.id)

  if (!hasPermission("inventory.manage")) return null

  const parsedDelta = Number(delta)
  const canSubmit = delta !== "" && Number.isInteger(parsedDelta) && parsedDelta !== 0 && reason.trim()

  function submit() {
    adjust.mutate(
      { delta: parsedDelta, reason: reason.trim() },
      {
        onSuccess: () => {
          toast.success("Stock adjusted.")
          setOpen(false)
          setDelta("")
          setReason("")
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Adjust
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Adjust stock — {stock.sku}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="delta">
              Change (positive to add, negative to remove)
            </Label>
            <Input
              id="delta"
              type="number"
              value={delta}
              onChange={(e) => setDelta(e.target.value)}
              placeholder="e.g. -2 or 5"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="reason">Reason</Label>
            <Input
              id="reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Damaged in warehouse"
            />
          </div>
        </div>
        <DialogFooter>
          <Button disabled={!canSubmit || adjust.isPending} onClick={submit}>
            {adjust.isPending ? "Adjusting..." : "Adjust stock"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StockSection() {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [search, setSearch] = React.useState("")
  const [lowStockOnly, setLowStockOnly] = React.useState(false)

  const query = useInventoryStock({ page, pageSize, q: search, low_stock_only: lowStockOnly })

  const columns: DataTableColumn<InventoryStock>[] = [
    { id: "sku", header: "SKU", cell: (row) => <span className="font-medium">{row.sku}</span> },
    { id: "product", header: "Product", cell: (row) => row.product_title },
    {
      id: "available",
      header: "Available",
      cell: (row) => (
        <span className={row.available_quantity <= 0 ? "text-red-600 font-semibold" : ""}>
          {row.available_quantity}
        </span>
      ),
    },
    {
      id: "shopify_qty",
      header: "Shopify qty (reference)",
      cell: (row) => <span className="text-muted-foreground">{row.inventory_quantity}</span>,
    },
    { id: "updated", header: "Updated", cell: (row) => formatDateTime(row.updated_at) },
    { id: "action", header: "", cell: (row) => <AdjustStockAction stock={row} /> },
  ]

  return (
    <div className="flex flex-col gap-4">
      <FilterBar
        searchValue={search}
        onSearchChange={(value) => {
          setSearch(value)
          resetPage()
        }}
        searchPlaceholder="Search by SKU or product..."
        extra={
          <Button
            variant={lowStockOnly ? "default" : "outline"}
            size="sm"
            onClick={() => {
              setLowStockOnly((v) => !v)
              resetPage()
            }}
          >
            Out of stock only
          </Button>
        }
      />
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
        isEmpty={(data) => data.data.length === 0}
        emptyTitle="No stock records found"
        emptyDescription="Stock levels appear here once a Shopify product sync has run."
      >
        {(data) => (
          <>
            <DataTable columns={columns} data={data.data} rowKey={(row) => row.id} />
            <PaginationBar meta={data.meta} onPageChange={setPage} />
          </>
        )}
      </QueryStates>
    </div>
  )
}

function MovementsSection() {
  const { page, pageSize, setPage } = usePaginationState()
  const query = useInventoryMovements({ page, pageSize })

  const columns: DataTableColumn<InventoryMovement>[] = [
    { id: "sku", header: "SKU", cell: (row) => row.sku ?? "—" },
    {
      id: "type",
      header: "Type",
      cell: (row) => <Badge variant="secondary">{movementTypeLabel(row.movement_type)}</Badge>,
    },
    {
      id: "delta",
      header: "Change",
      cell: (row) => (
        <span className={row.quantity_delta < 0 ? "text-red-600" : "text-emerald-600"}>
          {row.quantity_delta > 0 ? `+${row.quantity_delta}` : row.quantity_delta}
        </span>
      ),
    },
    { id: "after", header: "Balance after", cell: (row) => row.quantity_after },
    { id: "reason", header: "Reason", cell: (row) => row.reason ?? "—" },
    { id: "created", header: "When", cell: (row) => formatDateTime(row.created_at) },
  ]

  return (
    <div className="flex flex-col gap-4">
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
        isEmpty={(data) => data.data.length === 0}
        emptyTitle="No stock movements yet"
        emptyDescription="Dispatches, RTO restocks, and manual adjustments will appear here."
      >
        {(data) => (
          <>
            <DataTable columns={columns} data={data.data} rowKey={(row) => row.id} />
            <PaginationBar meta={data.meta} onPageChange={setPage} />
          </>
        )}
      </QueryStates>
    </div>
  )
}

export default function InventoryPage() {
  return (
    <>
      <PageHeader
        title="Inventory"
        description="Stock levels tracked from Shopify, dispatch, and RTO — independent of Shopify's own count."
      />
      <div className="flex flex-col gap-8">
        <StockSection />
        <div className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold">Movement History</h2>
          <MovementsSection />
        </div>
      </div>
    </>
  )
}
