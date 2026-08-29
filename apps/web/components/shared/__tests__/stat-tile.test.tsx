import { describe, expect, it } from "vitest"
import { screen } from "@testing-library/react"
import { ShoppingCart } from "lucide-react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { StatTile } from "@/components/shared/stat-tile"

describe("StatTile", () => {
  it("renders a plain (non-link) tile when no href is given", () => {
    const { container } = renderWithProviders(
      <StatTile label="Total Orders" value={100} icon={ShoppingCart} />
    )
    expect(screen.getByText("100")).toBeInTheDocument()
    expect(container.querySelector("a")).not.toBeInTheDocument()
  })

  it("renders subtext and a working drill-down link when href is given", () => {
    renderWithProviders(
      <StatTile
        label="COD Orders"
        value={70}
        icon={ShoppingCart}
        subtext="70.0% · ₹3,50,000"
        href="/orders?payment_type=cod"
      />
    )
    expect(screen.getByText("70.0% · ₹3,50,000", { exact: false })).toBeInTheDocument()
    const link = screen.getByText("COD Orders").closest("a")
    expect(link).toHaveAttribute("href", "/orders?payment_type=cod")
  })
})
