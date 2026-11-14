"use client"

import * as React from "react"
import { Download, RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { AmazonAccountSalesView } from "@/components/marketplaces/amazon-account-sales-view"
import { AmazonSalesView } from "@/components/marketplaces/amazon-sales-view"
import { PageHeader } from "@/components/shared/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime } from "@/lib/format"
import {
  startAmazonReports,
  useAmazonAccountSales,
  useAmazonExport,
  useAmazonOrdersProductReport,
  useAmazonReportPoll,
  useAmazonStatus,
} from "@/services/amazon-sales"
import type { ChunkRef } from "@/types/amazon-sales"

function isoDaysAgo(n: number): string {
  const d = new Date(Date.now() - n * 86400000)
  return d.toISOString().slice(0, 10)
}

export default function AmazonSalesAnalyticsPage() {
  const status = useAmazonStatus()
  const [accountFrom, setAccountFrom] = React.useState(isoDaysAgo(7))
  const [accountTo, setAccountTo] = React.useState(isoDaysAgo(1))
  const accountSales = useAmazonAccountSales(accountFrom, accountTo)
  const [from, setFrom] = React.useState(isoDaysAgo(7))
  const [to, setTo] = React.useState(isoDaysAgo(1))
  const [q, setQ] = React.useState("")
  const [ordersFrom, setOrdersFrom] = React.useState(isoDaysAgo(7))
  const [ordersTo, setOrdersTo] = React.useState(isoDaysAgo(1))
  const [ordersQ, setOrdersQ] = React.useState("")
  const ordersReport = useAmazonOrdersProductReport(ordersFrom, ordersTo)
  const [chunks, setChunks] = React.useState<ChunkRef[] | null>(null)
  const [starting, setStarting] = React.useState(false)
  const [startError, setStartError] = React.useState<string | null>(null)
  const poll = useAmazonReportPoll(chunks)
  const exportMut = useAmazonExport()

  const run = async () => {
    setStarting(true)
    setStartError(null)
    setChunks(null)
    try {
      setChunks(await startAmazonReports(from, to))
    } catch (e) {
      setStartError(getApiErrorMessage(e))
    } finally {
      setStarting(false)
    }
  }

  const st = status.data
  const error = startError ?? (poll.isError ? getApiErrorMessage(poll.error) : null)
  const processing = starting || poll.data?.state === "processing"
  const fullReport = poll.data?.report ?? null
  // Product filter is applied locally: typing must never trigger new Amazon calls.
  const report = React.useMemo(() => {
    if (!fullReport) return null
    const needle = q.trim().toLowerCase()
    if (!needle) return fullReport
    return {
      ...fullReport,
      products: fullReport.products.filter((p) =>
        [p.sku, p.asin, p.product_name].some((v) => (v ?? "").toLowerCase().includes(needle))
      ),
    }
  }, [fullReport, q])

  const ordersFullReport = ordersReport.data ?? null
  const ordersFilteredReport = React.useMemo(() => {
    if (!ordersFullReport) return null
    const needle = ordersQ.trim().toLowerCase()
    if (!needle) return ordersFullReport
    return {
      ...ordersFullReport,
      products: ordersFullReport.products.filter((p) =>
        [p.sku, p.asin, p.product_name].some((v) => (v ?? "").toLowerCase().includes(needle))
      ),
    }
  }, [ordersFullReport, ordersQ])

  return (
    <>
      <PageHeader
        title="Amazon · Sales Analytics"
        description="Read-only product sales from Amazon SP-API, fetched live. Nothing is stored in the OMS."
        actions={
          <Button
            variant="outline"
            disabled={!chunks || poll.data?.state !== "done" || exportMut.isPending}
            onClick={() =>
              chunks &&
              exportMut.mutate(chunks, {
                onError: (e) => toast.error(getApiErrorMessage(e)),
              })
            }
          >
            <Download className="mr-2 size-4" /> Export Excel
          </Button>
        }
      />

      {st && !st.configured && (
        <Alert className="mb-4" variant="destructive">
          <AlertTitle>Amazon is not connected</AlertTitle>
          <AlertDescription>
            Missing environment variables: {st.missing.join(", ")}. No data is shown until real
            credentials are configured.
          </AlertDescription>
        </Alert>
      )}

      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-sm">Account-level sales (Sales API)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm">
              From
              <Input
                type="date"
                value={accountFrom}
                max={accountTo}
                onChange={(e) => setAccountFrom(e.target.value)}
              />
            </label>
            <label className="text-sm">
              To
              <Input
                type="date"
                value={accountTo}
                min={accountFrom}
                onChange={(e) => setAccountTo(e.target.value)}
              />
            </label>
          </div>
          {st && !st.configured && (
            <Alert variant="destructive">
              <AlertTitle>Amazon is not connected</AlertTitle>
              <AlertDescription>
                Missing environment variables: {st.missing.join(", ")}.
              </AlertDescription>
            </Alert>
          )}
          {accountSales.isLoading && <Skeleton className="h-32 w-full" />}
          {accountSales.isError && (
            <Alert variant="destructive">
              <AlertTitle>Account-level sales request failed</AlertTitle>
              <AlertDescription>{getApiErrorMessage(accountSales.error)}</AlertDescription>
            </Alert>
          )}
          {accountSales.data && <AmazonAccountSalesView report={accountSales.data} />}
        </CardContent>
      </Card>

      <h2 className="mb-2 text-sm font-medium">
        Product-wise sales (Reports API -- requires Amazon authorization)
      </h2>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="text-sm">
          From
          <Input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label className="text-sm">
          To
          <Input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
        </label>
        <label className="text-sm">
          Product / SKU / ASIN
          <Input
            placeholder="Filter products"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        <Button onClick={run} disabled={processing || !st?.configured}>
          <RefreshCw className="mr-2 size-4" /> {processing ? "Fetching…" : "Fetch from Amazon"}
        </Button>
        <span className="text-muted-foreground text-xs">
          Last successful fetch:{" "}
          {st?.last_successful_fetch_at ? formatDateTime(st.last_successful_fetch_at) : "never"}
          {st ? ` · max range ${st.max_range_days} days` : ""}
        </span>
      </div>

      {error && (
        <Alert className="mb-4" variant="destructive">
          <AlertTitle>Amazon request failed</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {processing && (
        <div className="space-y-2">
          <p className="text-muted-foreground text-sm">
            Amazon is generating the report(s); this can take a few minutes. Checking every 5s…
          </p>
          <Skeleton className="h-40 w-full" />
        </div>
      )}

      {report && <AmazonSalesView report={report} />}

      <h2 className="mt-10 mb-2 text-sm font-medium">
        Product-wise sales (Orders API -- working alternative while Reports API is blocked)
      </h2>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="text-sm">
          From
          <Input
            type="date"
            value={ordersFrom}
            max={ordersTo}
            onChange={(e) => setOrdersFrom(e.target.value)}
          />
        </label>
        <label className="text-sm">
          To
          <Input
            type="date"
            value={ordersTo}
            min={ordersFrom}
            onChange={(e) => setOrdersTo(e.target.value)}
          />
        </label>
        <label className="text-sm">
          Product / SKU / ASIN
          <Input
            placeholder="Filter products"
            value={ordersQ}
            onChange={(e) => setOrdersQ(e.target.value)}
          />
        </label>
        <Button
          onClick={() => ordersReport.refetch()}
          disabled={ordersReport.isFetching || !st?.configured}
        >
          <RefreshCw className="mr-2 size-4" />{" "}
          {ordersReport.isFetching ? "Fetching…" : "Fetch from Amazon"}
        </Button>
      </div>

      {ordersReport.isFetching && (
        <div className="space-y-2">
          <p className="text-muted-foreground text-sm">
            Fetching orders and line items from Amazon (one call per order) -- this can take a
            while for a busy range…
          </p>
          <Skeleton className="h-40 w-full" />
        </div>
      )}
      {ordersReport.isError && (
        <Alert className="mb-4" variant="destructive">
          <AlertTitle>Orders API request failed</AlertTitle>
          <AlertDescription>{getApiErrorMessage(ordersReport.error)}</AlertDescription>
        </Alert>
      )}
      {ordersFilteredReport && <AmazonSalesView report={ordersFilteredReport} />}
    </>
  )
}
