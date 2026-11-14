import * as React from "react"

import { MarketplaceOrdersView } from "./_components/marketplace-orders-view"

export default function MarketplaceOrdersPage() {
  return (
    <React.Suspense fallback={null}>
      <MarketplaceOrdersView />
    </React.Suspense>
  )
}
