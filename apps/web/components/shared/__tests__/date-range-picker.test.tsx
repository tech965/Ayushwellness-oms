import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import {
  DateRangePicker,
  type DateRangeValue,
} from "@/components/shared/date-range-picker"
import { istMidnightFromLocalParts, istEndOfDayFromLocalParts } from "@/lib/ist-date"

function setup(value: DateRangeValue = {}) {
  const onChange = vi.fn()
  render(<DateRangePicker value={value} onChange={onChange} />)
  return onChange
}

/** Day cells render as buttons whose accessible name contains the date. */
function day(label: RegExp) {
  return screen.getAllByRole("button", { name: label })[0]
}

describe("DateRangePicker", () => {
  it("offers every required preset", () => {
    setup()
    for (const label of [
      "Today",
      "Yesterday",
      "Last 7 Days",
      "Last 30 Days",
      "This Month",
      "Last Month",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument()
    }
  })

  it("a preset applies immediately with IST day bounds", () => {
    const onChange = setup()
    fireEvent.click(screen.getByRole("button", { name: "Today" }))
    expect(onChange).toHaveBeenCalledTimes(1)
    const { from, to } = onChange.mock.calls[0][0] as DateRangeValue
    expect(to!.getTime() - from!.getTime()).toBe(24 * 60 * 60 * 1000 - 1)
  })

  it("keeps the calendar open after the start date and only applies on Apply", async () => {
    const user = userEvent.setup()
    const onChange = setup()
    await user.click(screen.getByTestId("date-range-trigger"))

    expect(screen.getByTestId("date-range-apply")).toBeDisabled()

    await user.click(day(/ 1st, /))
    // Start picked: popover still open, nothing sent to the page yet.
    // (react-day-picker treats the first click as a one-day range.)
    expect(screen.getByTestId("date-range-summary")).toHaveTextContent(
      /^1 \w{3} \d{4} – 1 \w{3} \d{4}$/
    )
    expect(onChange).not.toHaveBeenCalled()

    await user.click(day(/ 5th, /))
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByTestId("date-range-apply")).toBeEnabled()

    await user.click(screen.getByTestId("date-range-apply"))
    expect(onChange).toHaveBeenCalledTimes(1)
    const { from, to } = onChange.mock.calls[0][0] as DateRangeValue
    expect(to!.getTime()).toBeGreaterThan(from!.getTime())
    expect(screen.queryByTestId("date-range-apply")).not.toBeInTheDocument()
  })

  it("never produces an end date before the start date", async () => {
    const user = userEvent.setup()
    const onChange = setup()
    await user.click(screen.getByTestId("date-range-trigger"))
    await user.click(day(/ 20th, /))
    await user.click(day(/ 10th, /))
    await user.click(screen.getByTestId("date-range-apply"))
    const { from, to } = onChange.mock.calls[0][0] as DateRangeValue
    expect(to!.getTime()).toBeGreaterThanOrEqual(from!.getTime())
  })

  it("Clear removes the range", async () => {
    const user = userEvent.setup()
    const from = istMidnightFromLocalParts(new Date(2026, 9, 1))
    const to = istEndOfDayFromLocalParts(new Date(2026, 9, 7))
    const onChange = setup({ from, to })
    await user.click(screen.getByTestId("date-range-trigger"))
    await user.click(screen.getByTestId("date-range-clear"))
    expect(onChange).toHaveBeenCalledWith({ from: undefined, to: undefined })
  })

  it("shows the applied custom range clearly on the trigger", () => {
    const from = istMidnightFromLocalParts(new Date(2026, 9, 1))
    const to = istEndOfDayFromLocalParts(new Date(2026, 9, 7))
    setup({ from, to })
    expect(screen.getByTestId("date-range-trigger")).toHaveTextContent(
      "1 Oct 2026 – 7 Oct 2026"
    )
  })
})
