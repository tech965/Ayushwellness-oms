import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"

import { LiveResultNotice } from "@/components/shared/live-result-notice"

describe("LiveResultNotice", () => {
  it("renders nothing for an exact result", () => {
    const { container } = render(
      <LiveResultNotice
        meta={{ page: 1, page_size: 20, total_items: 5, total_pages: 1 }}
      />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing when there is no meta yet", () => {
    const { container } = render(<LiveResultNotice meta={undefined} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("flags an estimated total", () => {
    render(<LiveResultNotice meta={{ total_is_estimate: true, has_more: true }} />)
    expect(screen.getByTestId("live-result-estimate")).toHaveTextContent(
      /totals are estimated/i
    )
  })

  it("shows incomplete-result warnings prominently, ahead of the estimate note", () => {
    render(
      <LiveResultNotice
        meta={{
          total_is_estimate: true,
          warnings: ["Stopped after 20 Shopify pages; results are incomplete."],
        }}
      />
    )
    expect(screen.getByTestId("live-result-warning")).toHaveTextContent(
      /Results may be incomplete/i
    )
    expect(screen.getByTestId("live-result-warning")).toHaveTextContent(
      /Stopped after 20 Shopify pages/
    )
    expect(screen.queryByTestId("live-result-estimate")).toBeNull()
  })
})
