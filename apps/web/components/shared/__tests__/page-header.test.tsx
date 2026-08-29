import { describe, expect, it } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { PageHeader } from "@/components/shared/page-header"

describe("PageHeader", () => {
  it("renders without a back link by default (top-level pages unaffected)", () => {
    renderWithProviders(<PageHeader title="Orders" />)
    expect(screen.getByText("Orders")).toBeInTheDocument()
    expect(screen.queryByText("Back")).not.toBeInTheDocument()
  })

  it("renders a back link pointing at backHref when provided", () => {
    renderWithProviders(
      <PageHeader title="Order Breakdown" backHref="/dashboard" backLabel="Back to Dashboard" />
    )
    const backLink = screen.getByText("Back to Dashboard").closest("a")
    expect(backLink).toHaveAttribute("href", "/dashboard")
  })
})
