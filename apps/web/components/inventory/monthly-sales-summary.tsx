"use client"

import { Card, CardContent } from "@/components/ui/card"

interface MonthlySalesSummaryProps {
  /** Packets sold across every manual platform this IST calendar month
   * (Sale movements only) -- computed by the backend from real records.
   * `undefined` while loading / on error: nothing is rendered rather
   * than a fabricated "0".
   */
  soldPackets: number | undefined
}

/** ONE product-level "Sold This Month" figure shown above the Marketplace
 * Stock table -- never one card per SKU or per platform.
 */
export function MonthlySalesSummary({ soldPackets }: MonthlySalesSummaryProps) {
  if (soldPackets === undefined) return null
  return (
    <Card data-testid="monthly-sales-summary">
      <CardContent className="flex flex-wrap items-baseline justify-between gap-2 py-3">
        <span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
          Monthly Sales
        </span>
        <span className="text-sm">
          Sold This Month:{" "}
          <span className="font-semibold tabular-nums">
            {soldPackets.toLocaleString()} {soldPackets === 1 ? "packet" : "packets"}
          </span>
        </span>
      </CardContent>
    </Card>
  )
}
