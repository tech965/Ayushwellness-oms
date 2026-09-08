"use client"

import { FulfillmentDashboard } from "@/components/fulfillment/fulfillment-dashboard"
import { useMyShipmentAnalytics, useMyShipmentSummary } from "@/services/shipment-staff"

/** Reuses the exact same dashboard body as `/fulfillment/dashboard` --
 * only the data hooks change (scoped to the caller's own permitted
 * Telecallers, see `ShipmentStaffService.resolve_scope`). No global
 * company-wide figures are ever fetched here.
 */
export default function ShipmentStaffDashboardPage() {
  return (
    <FulfillmentDashboard
      useSummary={useMyShipmentSummary}
      useAnalytics={useMyShipmentAnalytics}
      title="My Shipment Dashboard"
      queueHref="/shipment-staff/orders"
      telecallerRowsClickable={false}
      showShipmentStaffPerformance={false}
    />
  )
}
