import { FulfillmentQueue } from "@/components/fulfillment/fulfillment-queue"

/** Legacy alias for the Fulfillment / Shipment Queue, now canonically at
 * `/fulfillment/orders`. Kept so existing bookmarks/links keep working.
 */
export default function ShipmentQueuePage() {
  return <FulfillmentQueue />
}
