import { FulfillmentDashboard } from "@/components/fulfillment/fulfillment-dashboard"
import { LiveModeGate } from "@/components/shared/live-mode-gate"
import { LiveUnavailable } from "@/components/shared/live-unavailable"

/** Legacy alias for the Fulfillment Dashboard, now canonically at
 * `/fulfillment/dashboard`. Kept so existing bookmarks/links keep working.
 */
function StoredDataShipmentDashboardPage() {
  return <FulfillmentDashboard />
}

/** While provider data is read live, this page shows its live replacement
 * (or an explanation); otherwise the stored-data screen is unchanged.
 */
export default function Page() {
  return (
    <LiveModeGate live={<LiveUnavailable title="Shipment dashboard" operation="shiprocket_aggregates" fallbackReason="Shipment totals and courier performance need a bulk Shiprocket status API that this integration does not have." />}>
      <StoredDataShipmentDashboardPage />
    </LiveModeGate>
  )
}
