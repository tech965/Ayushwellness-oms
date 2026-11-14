import * as React from "react"

import { MarketplaceOrdersView } from "../_components/marketplace-orders-view"

export default function AmazonOrdersPage() {
  return (
    <React.Suspense fallback={null}>
      <MarketplaceOrdersView fixedMarketplace="amazon" />
    </React.Suspense>
  )
}
