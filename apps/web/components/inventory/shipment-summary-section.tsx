import { CheckCircle2, PackageCheck, Truck, Undo2 } from "lucide-react"

import { QueryStates } from "@/components/shared/query-states"
import { StatTile } from "@/components/shared/stat-tile"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { ProductShipmentSummary } from "@/types/platform-inventory"

interface ShipmentSummarySectionProps {
  isLoading: boolean
  isError: boolean
  error: unknown
  data: ProductShipmentSummary | undefined
  onRetry: () => void
}

/** "Shipments" — In Transit / Out for Delivery / Delivered (selected
 * date) / RTO, for this product. `in_transit`/`out_for_delivery`/`rto`
 * are always LIVE current counts (shipment status has no historical
 * day-by-day record); only "Delivered" is scoped to the selected date —
 * labeled explicitly so the two are never confused with each other.
 */
export function ShipmentSummarySection({
  isLoading,
  isError,
  error,
  data,
  onRetry,
}: ShipmentSummarySectionProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Shipments</CardTitle>
      </CardHeader>
      <CardContent>
        <QueryStates isLoading={isLoading} isError={isError} error={error} data={data} onRetry={onRetry}>
          {(summary) => (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile
                label="In Transit (live)"
                value={`${summary.in_transit} boxes`}
                icon={Truck}
                accent="blue"
              />
              <StatTile
                label="Out for Delivery (live)"
                value={`${summary.out_for_delivery} boxes`}
                icon={PackageCheck}
                accent="violet"
              />
              <StatTile
                label="Delivered (selected date)"
                value={`${summary.delivered_on_date} boxes`}
                icon={CheckCircle2}
                accent="emerald"
              />
              <StatTile
                label="RTO (live)"
                value={`${summary.rto} boxes`}
                icon={Undo2}
                accent="amber"
              />
            </div>
          )}
        </QueryStates>
      </CardContent>
    </Card>
  )
}
