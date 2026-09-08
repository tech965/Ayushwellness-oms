import { FulfillmentDashboard } from "@/components/fulfillment/fulfillment-dashboard"

/** Legacy alias for the Fulfillment Dashboard, now canonically at
 * `/fulfillment/dashboard`. Kept so existing bookmarks/links keep working.
 */
export default function ShipmentDashboardPage() {
  return <FulfillmentDashboard />
}
