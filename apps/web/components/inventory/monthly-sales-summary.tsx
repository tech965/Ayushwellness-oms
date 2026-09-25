"use client"

import { Card, CardContent } from "@/components/ui/card"

interface MonthlySalesSummaryProps {
  /** Packets sold this IST calendar month (effective Sale movements only)
   * -- computed by the backend from real records. `undefined` while
   * loading / on error: nothing is rendered rather than a fabricated "0".
   */
  soldPackets: number | undefined
  /** Set for a variant-scoped product (Herbal Masala): the OMS-visible
   * variant ("Gold Packet") this figure belongs to. Omitted for a
   * product-level summary.
   */
  variantName?: string
}

/** "Sold This Month" -- ONE figure per marketplace scope: one for the
 * product, or (for a product tracked per variant) one per OMS-visible
 * variant. Never one per SKU, never one per platform.
 */
export function MonthlySalesSummary({ soldPackets, variantName }: MonthlySalesSummaryProps) {
  if (soldPackets === undefined) return null
  return (
    <Card data-testid="monthly-sales-summary">
      <CardContent className="flex flex-wrap items-baseline justify-between gap-2 py-3">
        <span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
          {variantName ? `Monthly Sales — ${variantName}` : "Monthly Sales"}
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
