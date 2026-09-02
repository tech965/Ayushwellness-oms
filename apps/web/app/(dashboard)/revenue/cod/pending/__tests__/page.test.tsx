import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import CodPendingFulfillmentPage from "@/app/(dashboard)/revenue/cod/pending/page"
import { useOrders } from "@/services/orders"

let searchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/revenue/cod/pending",
  useSearchParams: () => searchParams,
}))

vi.mock("@/services/orders", () => ({
  useOrders: vi.fn(),
}))

const mockedUseOrders = vi.mocked(useOrders)

function ordersResult(total: number) {
  return {
    data: { data: [], meta: { total_items: total } },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useOrders>
}

describe("CodPendingFulfillmentPage", () => {
  it("shows Fulfilled/Unfulfilled counts for Pending COD orders and links back to COD Revenue", () => {
    searchParams = new URLSearchParams()
    mockedUseOrders.mockImplementation((params) => {
      if (params.fulfillment_status === "fulfilled") return ordersResult(7)
      if (params.fulfillment_status === "unfulfilled") return ordersResult(3)
      return ordersResult(0)
    })

    renderWithProviders(<CodPendingFulfillmentPage />)

    expect(screen.getByText("Pending COD — Fulfillment Status")).toBeInTheDocument()
    expect(screen.getByText("Back to COD Revenue")).toBeInTheDocument()
    expect(screen.getByText("7")).toBeInTheDocument()
    expect(screen.getByText("3")).toBeInTheDocument()

    const fulfilledLink = screen.getByText("Fulfilled").closest("a")
    expect(fulfilledLink?.getAttribute("href")).toContain("fulfillment=fulfilled")
    const unfulfilledLink = screen.getByText("Unfulfilled").closest("a")
    expect(unfulfilledLink?.getAttribute("href")).toContain("fulfillment=unfulfilled")

    // No orders preview until a fulfillment status is actually selected.
    expect(screen.queryByText("Fulfilled Pending COD Orders")).not.toBeInTheDocument()
    expect(screen.queryByText("Unfulfilled Pending COD Orders")).not.toBeInTheDocument()
  })

  it("shows the matching orders preview, without a Payment Status column, once Fulfilled is selected", () => {
    searchParams = new URLSearchParams({ fulfillment: "fulfilled" })
    mockedUseOrders.mockImplementation((params) => {
      if (params.fulfillment_status === "fulfilled") return ordersResult(7)
      if (params.fulfillment_status === "unfulfilled") return ordersResult(3)
      return ordersResult(0)
    })

    renderWithProviders(<CodPendingFulfillmentPage />)

    expect(screen.getByText("Fulfilled Pending COD Orders")).toBeInTheDocument()
    expect(screen.queryByText("Payment Status")).not.toBeInTheDocument()

    const viewAllLink = screen.getByText("View all").closest("a")
    expect(viewAllLink?.getAttribute("href")).toContain("payment_type=cod")
    expect(viewAllLink?.getAttribute("href")).toContain("payment_status=pending")
    expect(viewAllLink?.getAttribute("href")).toContain("fulfillment_status=fulfilled")
  })
})
