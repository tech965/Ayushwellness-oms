import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { StockDatePicker } from "@/components/shared/stock-date-picker"
import { istDateKey, istStartOfDay } from "@/lib/ist-date"

describe("StockDatePicker", () => {
  it("shows Today as active when value is today's IST date", () => {
    const today = istStartOfDay(new Date())
    render(<StockDatePicker value={today} onChange={vi.fn()} />)

    const todayButton = screen.getByRole("button", { name: "Today" })
    expect(todayButton).toHaveAttribute("aria-pressed", "true")
  })

  it("calls onChange with yesterday's IST midnight when Yesterday is clicked", async () => {
    const user = userEvent.setup()
    const today = istStartOfDay(new Date())
    const onChange = vi.fn()
    render(<StockDatePicker value={today} onChange={onChange} />)

    await user.click(screen.getByRole("button", { name: "Yesterday" }))

    expect(onChange).toHaveBeenCalledTimes(1)
    const passed = onChange.mock.calls[0][0] as Date
    const expectedYesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000)
    expect(passed.getTime()).toBe(expectedYesterday.getTime())
  })

  it("shows a formatted date label for a non-today/yesterday value", () => {
    const pastDate = istStartOfDay(new Date("2026-09-01T10:00:00Z"))
    render(<StockDatePicker value={pastDate} onChange={vi.fn()} />)

    expect(screen.getByText("1 Sep 2026")).toBeInTheDocument()
  })

  it("never lets Today and Yesterday both read as active at once", () => {
    const pastDate = istStartOfDay(new Date("2026-08-01T10:00:00Z"))
    render(<StockDatePicker value={pastDate} onChange={vi.fn()} />)

    expect(screen.getByRole("button", { name: "Today" })).toHaveAttribute(
      "aria-pressed",
      "false"
    )
    expect(screen.getByRole("button", { name: "Yesterday" })).toHaveAttribute(
      "aria-pressed",
      "false"
    )
  })
})

describe("istDateKey integration with StockDatePicker's value contract", () => {
  it("the value StockDatePicker hands back always formats to a clean YYYY-MM-DD", async () => {
    const user = userEvent.setup()
    const today = istStartOfDay(new Date())
    const onChange = vi.fn()
    render(<StockDatePicker value={today} onChange={onChange} />)

    await user.click(screen.getByRole("button", { name: "Yesterday" }))
    const passed = onChange.mock.calls[0][0] as Date
    expect(istDateKey(passed)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})
