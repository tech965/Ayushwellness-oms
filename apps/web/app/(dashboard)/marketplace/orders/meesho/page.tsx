import * as React from "react"

import { MarketplaceOrdersView } from "../_components/marketplace-orders-view"

export default function MeeshoOrdersPage() {
  return (
    <React.Suspense fallback={null}>
      <MarketplaceOrdersView fixedMarketplace="meesho" />
    </React.Suspense>
  )
}
