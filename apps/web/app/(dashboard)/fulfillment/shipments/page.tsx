import ShipmentsPage from "@/app/(dashboard)/shipments/page"

/** The Fulfillment module's shipment list + tracking view. Reuses the
 * existing `/shipments` page wholesale (AWB / status / courier / date
 * search, row -> `/shipments/[id]` processing + tracking timeline) —
 * no duplicate implementation.
 */
export default function FulfillmentShipmentsPage() {
  return <ShipmentsPage />
}
