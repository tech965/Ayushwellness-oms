"use client"

import * as React from "react"
import { useParams } from "next/navigation"

import { fmt, StockBadge } from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { ProductThumbnail } from "@/components/shared/product-thumbnail"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useCatalogProduct, useWarehouses } from "@/services/ims"

export default function ImsProductDetailPage() {
  const { id } = useParams<{ id: string }>()
  const query = useCatalogProduct(id)
  const whs = useWarehouses()

  return (
    <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()}>
      {(p) => (
        <div className="space-y-4">
          <PageHeader title={p.title} description={`${p.category} · ${p.vendor ?? "—"}`} backHref="/ims/products" backLabel="Products" />
          <Card>
            <CardHeader className="flex-row items-center gap-4">
              <ProductThumbnail src={p.image_url} alt={p.title} size="size-16" />
              <div className="space-y-1">
                <Badge variant={p.status === "active" ? "default" : "outline"}>{p.status}</Badge>
                <p className="text-muted-foreground text-sm">{p.variants.length} variant(s)/SKU(s)</p>
              </div>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">SKU inventory by warehouse</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Variant</TableHead>
                    <TableHead>SKU</TableHead>
                    <TableHead>Barcode</TableHead>
                    {(whs.data ?? []).map((w) => (
                      <TableHead key={w.id} className="text-right">
                        {w.code}
                      </TableHead>
                    ))}
                    <TableHead className="text-right">Total</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {p.variants.map((v) => (
                    <TableRow key={v.variant_id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <ProductThumbnail src={v.image_url} alt={v.title ?? p.title} size="size-8" />
                          {v.title}
                        </div>
                      </TableCell>
                      <TableCell>
                        {v.sku_missing ? <Badge variant="destructive">Missing SKU</Badge> : <span className="font-mono text-xs">{v.sku}</span>}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{v.barcode ?? "—"}</TableCell>
                      {(whs.data ?? []).map((w) => (
                        <TableCell key={w.id} className="text-right tabular-nums">
                          {fmt(v.by_warehouse?.find((x) => x.id === w.id)?.quantity ?? 0)}
                        </TableCell>
                      ))}
                      <TableCell className="text-right font-medium tabular-nums">{fmt(v.total_stock)}</TableCell>
                      <TableCell>
                        <StockBadge status={v.status ?? "not_tracked"} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <p className="text-muted-foreground mt-3 text-xs">
                “Not tracked” means no stock has been recorded for that SKU in the IMS yet. Record stock through a return, adjustment or opening balance.
              </p>
            </CardContent>
          </Card>
        </div>
      )}
    </QueryStates>
  )
}
