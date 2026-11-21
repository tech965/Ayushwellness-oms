import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"

import { FilterBar } from "@/components/shared/filter-bar"

const range = {
  from: new Date("2026-10-22T18:30:00Z"),
  to: new Date("2026-11-21T18:29:59Z"),
}

describe("FilterBar Clear button", () => {
  it("is hidden when only the page's default date range is active", () => {
    render(<FilterBar dateRange={range} dateRangeIsDefault onDateRangeChange={vi.fn()} />)
    expect(screen.queryByRole("button", { name: /^clear$/i })).not.toBeInTheDocument()
  })

  it("is shown when the viewer picked a date range", () => {
    render(<FilterBar dateRange={range} onDateRangeChange={vi.fn()} />)
    expect(screen.getByRole("button", { name: /^clear$/i })).toBeInTheDocument()
  })

  it("is shown when another filter is active even with the default range", () => {
    render(
      <FilterBar
        dateRange={range}
        dateRangeIsDefault
        onDateRangeChange={vi.fn()}
        statusValue="cod"
        onStatusChange={vi.fn()}
        statusOptions={[{ label: "COD", value: "cod" }]}
      />
    )
    expect(screen.getByRole("button", { name: /^clear$/i })).toBeInTheDocument()
  })
})
