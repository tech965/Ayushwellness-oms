import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { ClickableDonut, datumKey } from "@/components/shared/clickable-charts"
import { SelectableStatCard } from "@/components/shared/selectable-stat-card"

describe("datumKey", () => {
  it("reads the key from a recharts wrapper or a plain datum", () => {
    expect(datumKey({ payload: { key: "a" } })).toBe("a")
    expect(datumKey({ key: "b" })).toBe("b")
    expect(datumKey(null)).toBeUndefined()
    expect(datumKey({ payload: {} })).toBeUndefined()
  })
})

describe("ClickableDonut", () => {
  const data = [
    { key: "delivered", label: "Delivered", value: 30, color: "#65a30d" },
    { key: "rto", label: "Return to Origin", value: 10, color: "#f97316" },
    { key: "none", label: "Nothing", value: 0 },
  ]

  it("lists non-empty categories with exact counts and percentages", () => {
    render(
      <ClickableDonut
        data={data}
        selectedKey={null}
        onSelect={vi.fn()}
        centerLabel="orders"
        ariaLabel="status chart"
      />
    )
    expect(screen.getByRole("button", { name: /Delivered/ })).toHaveTextContent(
      "30 · 75%"
    )
    expect(screen.getByRole("button", { name: /Return to Origin/ })).toHaveTextContent(
      "10 · 25%"
    )
    expect(screen.queryByText("Nothing")).not.toBeInTheDocument()
  })

  it("reports the clicked category and shows the selected state", async () => {
    const onSelect = vi.fn()
    render(
      <ClickableDonut
        data={data}
        selectedKey="rto"
        onSelect={onSelect}
        centerLabel="orders"
        ariaLabel="status chart"
      />
    )
    await userEvent.click(screen.getByRole("button", { name: /Delivered/ }))
    expect(onSelect).toHaveBeenCalledWith("delivered")
    expect(screen.getByRole("button", { name: /Return to Origin/ })).toHaveAttribute(
      "aria-pressed",
      "true"
    )
  })

  it("says so when there is nothing to chart", () => {
    render(
      <ClickableDonut
        data={[{ key: "a", label: "A", value: 0 }]}
        selectedKey={null}
        onSelect={vi.fn()}
        centerLabel="orders"
        ariaLabel="status chart"
      />
    )
    expect(screen.getByText("No orders in the selected range.")).toBeInTheDocument()
  })
})

describe("SelectableStatCard", () => {
  it("is a keyboard-operable button with a selected state", async () => {
    const onClick = vi.fn()
    const { rerender } = render(
      <SelectableStatCard
        label="Total Orders"
        value={12}
        selected={false}
        onClick={onClick}
      />
    )
    const card = screen.getByRole("button", { name: /Total Orders/ })
    expect(card).toHaveAttribute("aria-pressed", "false")
    await userEvent.click(card)
    expect(onClick).toHaveBeenCalledTimes(1)
    rerender(
      <SelectableStatCard label="Total Orders" value={12} selected onClick={onClick} />
    )
    expect(screen.getByRole("button", { name: /Total Orders/ })).toHaveAttribute(
      "aria-pressed",
      "true"
    )
  })
})
