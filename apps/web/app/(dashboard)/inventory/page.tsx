"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { Eye } from "lucide-react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { ProductThumbnail } from "@/components/shared/product-thumbnail"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import { usePaginationState } from "@/lib/use-pagination"
import { useInventoryProducts } from "@/services/inventory"
import { useSettings, useUpdateSettings } from "@/services/settings"
import {
  STOCK_STATUS_BADGE_CLASSES,
  STOCK_STATUS_LABELS,
  type InventoryProductSummary,
} from "@/types/inventory"

/** Low-stock threshold is org-wide config (`AppSettings.values.inventory`,
 * `PUT /settings` behind `settings.manage`) -- reuses the existing
 * settings infrastructure rather than inventing inventory-specific
 * storage. Read-only for anyone who can see this page; editable only for
 * whoever actually holds `settings.manage`, matching the backend gate
 * exactly. The frontend never hardcodes or computes the low/out-of-stock
 * rule itself -- `stock_status` always comes from the API.
 */
function LowStockThresholdControl() {
  const { hasPermission } = useAuth()
  const canEditSettings = hasPermission("settings.manage")
  const query = useSettings()
  const update = useUpdateSettings()
  const [value, setValue] = React.useState("")
  const [editing, setEditing] = React.useState(false)

  const threshold = query.data?.settings.inventory.low_stock_threshold

  if (query.isLoading || threshold === undefined) return null

  function startEditing() {
    setValue(String(threshold))
    setEditing(true)
  }

  const parsed = Number(value)
  const canSubmit = value !== "" && Number.isInteger(parsed) && parsed >= 0

  function submit() {
    update.mutate(
      { inventory: { low_stock_threshold: parsed } },
      {
        onSuccess: () => {
          toast.success("Low-stock threshold updated.")
          setEditing(false)
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <Label htmlFor="low-stock-threshold" className="text-muted-foreground text-sm font-normal">
          Low-stock threshold (boxes)
        </Label>
        <Input
          id="low-stock-threshold"
          type="number"
          min={0}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="h-8 w-20"
        />
        <Button size="sm" disabled={!canSubmit || update.isPending} onClick={submit}>
          {update.isPending ? "Saving..." : "Save"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
          Cancel
        </Button>
      </div>
    )
  }

  return (
    <div className="text-muted-foreground flex items-center gap-2 text-sm">
      <span>
        Low-stock threshold: <span className="text-foreground font-medium">{threshold} boxes</span>
      </span>
      {canEditSettings && (
        <Button variant="link" size="sm" className="h-auto p-0" onClick={startEditing}>
          Edit
        </Button>
      )}
    </div>
  )
}

export default function InventoryPage() {
  const router = useRouter()
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [search, setSearch] = React.useState("")

  const query = useInventoryProducts({ page, pageSize, q: search })

  const columns: DataTableColumn<InventoryProductSummary>[] = [
    {
      id: "image",
      header: "",
      className: "w-12",
      cell: (product) => (
        <ProductThumbnail src={product.image_url} alt={product.display_title} size="size-8" />
      ),
    },
    {
      id: "title",
      header: "Product",
      cell: (product) => <span className="font-medium">{product.display_title}</span>,
    },
    { id: "vendor", header: "Vendor", cell: (product) => product.vendor ?? "—" },
    {
      id: "variants",
      header: "Variants",
      cell: (product) => product.variant_count,
    },
    {
      id: "boxes",
      header: "Total boxes",
      cell: (product) => (
        <span className={product.total_available_boxes <= 0 ? "font-semibold text-red-600" : ""}>
          {product.total_available_boxes}
        </span>
      ),
    },
    {
      id: "packets",
      header: "Total packets",
      cell: (product) => (
        <span className="text-muted-foreground">{product.total_packets.toLocaleString()}</span>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (product) => (
        <Badge
          variant="outline"
          className={cn("rounded-md border-transparent font-semibold", STOCK_STATUS_BADGE_CLASSES[product.stock_status])}
        >
          {STOCK_STATUS_LABELS[product.stock_status]}
        </Badge>
      ),
    },
    {
      id: "updated",
      header: "Updated",
      cell: (product) => formatDateTime(product.updated_at),
    },
    {
      id: "action",
      header: "",
      cell: (product) => (
        <Button
          variant="outline"
          size="sm"
          onClick={(e) => {
            e.stopPropagation()
            router.push(`/inventory/${product.id}`)
          }}
        >
          <Eye className="size-4" />
          View Inventory
        </Button>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Inventory"
        description="Stock is owned by the OMS and moved by Shiprocket dispatch/RTO events — never by Shopify's own count. Click a product to view and edit its variants."
      />
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <FilterBar
            searchValue={search}
            onSearchChange={(value) => {
              setSearch(value)
              resetPage()
            }}
            searchPlaceholder="Search by product name or SKU..."
            className="flex-1"
          />
          <LowStockThresholdControl />
        </div>
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No products found"
          emptyDescription="Products appear here once a Shopify product sync has run."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(product) => product.id}
                onRowClick={(product) => router.push(`/inventory/${product.id}`)}
              />
              <PaginationBar meta={data.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}
