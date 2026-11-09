import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { AmazonSalesView, money, num } from "@/components/marketplaces/amazon-sales-view"
import { navGroups } from "@/lib/navigation"
import type { SalesReport } from "@/types/amazon-sales"

vi.mock("recharts", async () => {
  const Pass = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>
  return {
    ResponsiveContainer: Pass,
    LineChart: Pass,
    BarChart: Pass,
    Line: Pass,
    Bar: Pass,
    XAxis: Pass,
    YAxis: Pass,
    CartesianGrid: Pass,
    Tooltip: Pass,
  }
})

const report: SalesReport = {
  date_from: "2026-11-01",
  date_to: "2026-11-02",
  fetched_at: "2026-11-09T00:00:00Z",
  summary: {
    currency: "INR",
    total_units_ordered: 15,
    total_ordered_revenue: "1500.00",
    products_sold: 2,
    total_units_shipped: null,
    total_shipped_revenue: null,
    total_units_refunded: null,
  },
  daily: [],
  weekly: [],
  monthly: [],
  products: [
    {
      asin: "B001",
      sku: "SKU-A",
      product_name: null,
      units_ordered: 10,
      units_shipped: null,
      ordered_revenue: "1000.50",
      avg_selling_price: "100.05",
    },
  ],
  unavailable: { "product.units_shipped": "No shipped units per product." },
  warnings: [],
  definitions: { ordered_revenue: "Ordered sales, not profit." },
}

describe("AmazonSalesView", () => {
  it("shows unavailable metrics as Unavailable, never as 0", () => {
    renderWithProviders(<AmazonSalesView report={report} />)
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(2)
    expect(screen.getByText("SKU-A")).toBeInTheDocument()
    expect(screen.getByText("Sales revenue, not profit")).toBeInTheDocument()
    expect(screen.getByText("Ordered sales, not profit.", { exact: false })).toBeInTheDocument()
  })

  it("formats helpers", () => {
    expect(num(null)).toBe("Unavailable")
    expect(num(0)).toBe("0")
    expect(money(null, "INR")).toBe("Unavailable")
  })
})

describe("navigation", () => {
  it("has Marketplaces > Amazon Sales Analytics", () => {
    const g = navGroups.find((x) => x.label === "Marketplaces")
    expect(g?.items[0].href).toBe("/marketplaces/amazon/sales-analytics")
  })
})
