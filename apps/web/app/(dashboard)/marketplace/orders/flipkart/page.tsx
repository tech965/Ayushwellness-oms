import * as React from "react"

import { MarketplaceOrdersView } from "../_components/marketplace-orders-view"

export default function FlipkartOrdersPage() {
  return (
    <React.Suspense fallback={null}>
      <MarketplaceOrdersView fixedMarketplace="flipkart" />
    </React.Suspense>
  )
}
