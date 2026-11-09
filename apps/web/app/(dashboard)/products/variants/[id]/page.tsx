"use client"

import Link from "next/link"
import { useParams } from "next/navigation"

import { PageHeader } from "@/components/shared/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatMoney } from "@/lib/format"
import { useLiveVariant } from "@/services/live"

/** One Shopify variant, read live, next to its OMS-owned stock. Only
 * variants already mapped to an OMS variant can be opened. */
export default function VariantDetailPage() {
  const params = useParams<{ id: string }>()
  const query = useLiveVariant(params.id)
  const variant = query.data

  return (
    <>
      <PageHeader
        title={variant ? `${variant.product_title} — ${variant.title ?? variant.sku}` : "Variant"}
        description="Catalogue fields are read live from Shopify. Stock is OMS-owned."
        backHref="/products"
        backLabel="Back to Products"
      />
      {query.isLoading ? <Skeleton className="h-40 w-full" /> : null}
      {query.isError ? (
        <Alert variant="destructive">
          <AlertTitle>Could not open this variant</AlertTitle>
          <AlertDescription>{getApiErrorMessage(query.error)}</AlertDescription>
        </Alert>
      ) : null}
      {variant ? (
        <div className="grid gap-4 md:grid-cols-2" data-testid="variant-detail">
          <Card>
            <CardHeader>
              <CardTitle>From Shopify (live)</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-1 text-sm">
              <div>SKU: {variant.sku ?? "—"}</div>
              <div>Price: {variant.price ? formatMoney(variant.price) : "—"}</div>
              {variant.compare_at_price ? (
                <div>Compare at: {formatMoney(variant.compare_at_price)}</div>
              ) : null}
              <div>Barcode: {variant.barcode ?? "—"}</div>
              <div className="text-muted-foreground">
                Shopify inventory: {variant.shopify_inventory_quantity ?? "—"} (information only,
                never used as OMS stock)
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>OMS stock</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-1 text-sm">
              {variant.oms ? (
                <>
                  <div>Physical: {variant.oms.physical_quantity} boxes</div>
                  <div>Reserved: {variant.oms.reserved_quantity} boxes</div>
                  <div>Available to promise: {variant.oms.available_to_promise} boxes</div>
                  <div className="text-muted-foreground">
                    {variant.oms.packets_per_box} packets per box · pack size {variant.oms.pack_size}
                  </div>
                  <Link className="underline" href="/inventory">
                    Open inventory ledger
                  </Link>
                </>
              ) : (
                <p className="text-muted-foreground">Not mapped to OMS stock.</p>
              )}
            </CardContent>
          </Card>
        </div>
      ) : null}
    </>
  )
}
