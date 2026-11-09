"use client"

import * as React from "react"
import Link from "next/link"
import { toast } from "sonner"

import { LiveResultNotice } from "@/components/shared/live-result-notice"
import { PageHeader } from "@/components/shared/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatMoney } from "@/lib/format"
import { useAutoMapBySku, useLiveProducts } from "@/services/live"

/** Products and variants read live from Shopify, with the OMS-owned stock
 * (physical / reserved / available to promise) alongside. Shopify's own
 * inventory count is shown only as information and is never used as stock. */
export function LiveCatalog() {
  const { hasPermission } = useAuth()
  const [q, setQ] = React.useState("")
  const [sku, setSku] = React.useState("")
  const [status, setStatus] = React.useState("")
  const [mapped, setMapped] = React.useState<boolean | undefined>(undefined)
  const [cursors, setCursors] = React.useState<string[]>([])
  const cursor = cursors[cursors.length - 1]

  const query = useLiveProducts({ q, sku, status, mapped, cursor })
  const autoMap = useAutoMapBySku()

  function resetPaging() {
    setCursors([])
  }

  async function runAutoMap() {
    try {
      const result = await autoMap.mutateAsync()
      toast.success(
        `Mapped ${result?.mapped.length ?? 0} variant(s); ${result?.unmatched.length ?? 0} had no exact SKU match.`
      )
      if (result && !result.complete) toast.warning("Not every Shopify page was scanned.")
      void query.refetch()
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  return (
    <>
      <PageHeader
        title="Products"
        description="Catalogue read live from Shopify. Stock figures are OMS-owned."
        actions={
          hasPermission("inventory.manage") ? (
            <Button size="sm" variant="outline" disabled={autoMap.isPending} onClick={() => void runAutoMap()}>
              Map variants by SKU
            </Button>
          ) : null
        }
      />
      <div className="flex flex-wrap items-center gap-2 pb-3">
        <Input
          className="w-56"
          placeholder="Search title"
          aria-label="Search title"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            resetPaging()
          }}
        />
        <Input
          className="w-44"
          placeholder="SKU"
          aria-label="SKU"
          value={sku}
          onChange={(e) => {
            setSku(e.target.value)
            resetPaging()
          }}
        />
        <select
          aria-label="Status"
          className="border-input h-9 rounded-md border bg-transparent px-2 text-sm"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value)
            resetPaging()
          }}
        >
          <option value="">All statuses</option>
          <option value="active">Active</option>
          <option value="draft">Draft</option>
          <option value="archived">Archived</option>
        </select>
        <select
          aria-label="Mapping"
          className="border-input h-9 rounded-md border bg-transparent px-2 text-sm"
          value={mapped === undefined ? "" : String(mapped)}
          onChange={(e) => {
            setMapped(e.target.value === "" ? undefined : e.target.value === "true")
            resetPaging()
          }}
        >
          <option value="">Mapped and unmapped</option>
          <option value="true">Mapped to OMS stock</option>
          <option value="false">Not mapped</option>
        </select>
      </div>

      {query.isError ? (
        <Alert variant="destructive">
          <AlertTitle>Could not load products from Shopify</AlertTitle>
          <AlertDescription>{getApiErrorMessage(query.error)}</AlertDescription>
        </Alert>
      ) : null}
      {query.isLoading ? <Skeleton className="h-40 w-full" /> : null}
      {query.data ? (
        <>
          <LiveResultNotice
            meta={
              query.data.page_filtered
                ? {
                    total_is_estimate: true,
                    has_more: query.data.has_more,
                  }
                : undefined
            }
          />
          {query.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">No products match on this page.</p>
          ) : (
            <div className="flex flex-col gap-4" data-testid="live-catalog">
              {query.data.items.map((product) => (
                <section key={product.external_product_id} className="rounded-lg border p-3">
                  <header className="flex items-center justify-between gap-2 pb-2">
                    <h3 className="font-medium">{product.title}</h3>
                    <Badge variant="outline">{product.status ?? "—"}</Badge>
                  </header>
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-muted-foreground">
                        <th className="py-1">Variant</th>
                        <th>SKU</th>
                        <th className="text-right">Price</th>
                        <th className="text-right">Physical</th>
                        <th className="text-right">Reserved</th>
                        <th className="text-right">Available</th>
                      </tr>
                    </thead>
                    <tbody>
                      {product.variants.map((variant) => (
                        <tr key={variant.external_variant_id} className="border-t">
                          <td className="py-1">
                            {variant.mapped ? (
                              <Link
                                className="underline"
                                href={`/products/variants/${variant.external_variant_id}`}
                              >
                                {variant.title ?? variant.sku ?? "Variant"}
                              </Link>
                            ) : (
                              (variant.title ?? "—")
                            )}
                          </td>
                          <td>{variant.sku ?? "—"}</td>
                          <td className="text-right tabular-nums">
                            {variant.price ? formatMoney(variant.price) : "—"}
                          </td>
                          {variant.oms ? (
                            <>
                              <td className="text-right tabular-nums">{variant.oms.physical_quantity}</td>
                              <td className="text-right tabular-nums">{variant.oms.reserved_quantity}</td>
                              <td className="text-right tabular-nums">{variant.oms.available_to_promise}</td>
                            </>
                          ) : (
                            <td colSpan={3} className="text-right text-muted-foreground">
                              Not mapped to OMS stock
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2 pt-3">
            <Button
              variant="outline"
              size="sm"
              disabled={cursors.length === 0}
              onClick={() => setCursors(cursors.slice(0, -1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!query.data.has_more || !query.data.next_cursor}
              onClick={() => query.data?.next_cursor && setCursors([...cursors, query.data.next_cursor])}
            >
              Next
            </Button>
          </div>
        </>
      ) : null}
    </>
  )
}
