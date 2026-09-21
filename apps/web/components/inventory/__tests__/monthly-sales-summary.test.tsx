import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"

import { MonthlySalesSummary } from "@/components/inventory/monthly-sales-summary"

describe("MonthlySalesSummary", () => {
  it("shows the backend-provided figure", () => {
    render(<MonthlySalesSummary soldPackets={25} />)
    expect(screen.getByText("Monthly Sales")).toBeInTheDocument()
    expect(screen.getByText("Sold This Month:")).toBeInTheDocument()
    expect(screen.getByText("25 packets")).toBeInTheDocument()
  })

  it("shows 0 packets when nothing was sold", () => {
    render(<MonthlySalesSummary soldPackets={0} />)
    expect(screen.getByText("0 packets")).toBeInTheDocument()
  })

  it("uses the singular for exactly one packet", () => {
    render(<MonthlySalesSummary soldPackets={1} />)
    expect(screen.getByText("1 packet")).toBeInTheDocument()
  })

  it("renders nothing (never a fabricated 0) while the figure is unknown", () => {
    const { container } = render(<MonthlySalesSummary soldPackets={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })
})
