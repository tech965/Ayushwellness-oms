import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"

import { QueryStates } from "@/components/shared/query-states"

describe("QueryStates", () => {
  it("renders skeletons while loading", () => {
    const { container } = render(
      <QueryStates isLoading isError={false} data={undefined}>
        {() => <div>content</div>}
      </QueryStates>
    )
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
    expect(screen.queryByText("content")).not.toBeInTheDocument()
  })

  it("renders an error message and retry button on error", () => {
    const onRetry = vi.fn()
    render(
      <QueryStates
        isLoading={false}
        isError
        error={new Error("Boom")}
        data={undefined}
        onRetry={onRetry}
      >
        {() => <div>content</div>}
      </QueryStates>
    )
    expect(screen.getByText("Boom")).toBeInTheDocument()
    screen.getByRole("button", { name: /retry/i }).click()
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it("renders the empty state when isEmpty returns true", () => {
    render(
      <QueryStates
        isLoading={false}
        isError={false}
        data={[]}
        isEmpty={(data) => data.length === 0}
        emptyTitle="Nothing found"
      >
        {() => <div>content</div>}
      </QueryStates>
    )
    expect(screen.getByText("Nothing found")).toBeInTheDocument()
    expect(screen.queryByText("content")).not.toBeInTheDocument()
  })

  it("renders children when data is present", () => {
    render(
      <QueryStates isLoading={false} isError={false} data={["a"]}>
        {(data) => <div>{data.length} item</div>}
      </QueryStates>
    )
    expect(screen.getByText("1 item")).toBeInTheDocument()
  })

  // Pre-demo fix: a background refetch failing on an already-loaded page
  // must never blank the existing content -- that read as the whole page
  // "blinking" back to an error/blank state during ordinary navigation.
  it("keeps showing already-loaded data when a background refetch fails, instead of blanking to the error state", () => {
    const onRetry = vi.fn()
    render(
      <QueryStates
        isLoading={false}
        isError
        error={new Error("Network hiccup")}
        data={["a", "b"]}
        onRetry={onRetry}
      >
        {(data) => <div>{data.length} items loaded</div>}
      </QueryStates>
    )

    // The real content is still there...
    expect(screen.getByText("2 items loaded")).toBeInTheDocument()
    // ...the blocking full-page error Alert is not...
    expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument()
    // ...and a small, non-blocking notice (with its own Retry) is shown
    // instead.
    expect(screen.getByText(/Could not refresh/)).toBeInTheDocument()
    screen.getByRole("button", { name: /retry/i }).click()
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it("still shows the full blocking error state when a query has never loaded any data", () => {
    render(
      <QueryStates isLoading={false} isError error={new Error("Boom")} data={undefined}>
        {() => <div>content</div>}
      </QueryStates>
    )
    expect(screen.getByText("Something went wrong")).toBeInTheDocument()
    expect(screen.queryByText("content")).not.toBeInTheDocument()
  })
})
