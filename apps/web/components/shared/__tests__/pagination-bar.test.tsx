import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"

import { PaginationBar } from "@/components/shared/pagination-bar"

describe("PaginationBar", () => {
  it("renders nothing when there is only one page", () => {
    const { container } = render(
      <PaginationBar
        meta={{ page: 1, page_size: 20, total_items: 5, total_pages: 1 }}
        onPageChange={vi.fn()}
      />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it("disables Previous on the first page and Next on the last page", () => {
    render(
      <PaginationBar
        meta={{ page: 1, page_size: 20, total_items: 40, total_pages: 2 }}
        onPageChange={vi.fn()}
      />
    )
    expect(screen.getByRole("button", { name: /previous/i })).toBeDisabled()
    expect(screen.getByRole("button", { name: /next/i })).not.toBeDisabled()
  })

  it("calls onPageChange with the next/previous page number", () => {
    const onPageChange = vi.fn()
    render(
      <PaginationBar
        meta={{ page: 2, page_size: 20, total_items: 60, total_pages: 3 }}
        onPageChange={onPageChange}
      />
    )
    screen.getByRole("button", { name: /next/i }).click()
    expect(onPageChange).toHaveBeenCalledWith(3)
    screen.getByRole("button", { name: /previous/i }).click()
    expect(onPageChange).toHaveBeenCalledWith(1)
  })
})
