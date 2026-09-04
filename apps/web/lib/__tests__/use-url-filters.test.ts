import { describe, expect, it, vi } from "vitest"
import { renderHook } from "@testing-library/react"

import { useUrlFilters } from "@/lib/use-url-filters"

const replace = vi.fn()
let searchParamsString = ""

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn(), back: vi.fn() }),
  usePathname: () => "/orders",
  useSearchParams: () => new URLSearchParams(searchParamsString),
}))

const FILTER_DEFAULTS = { q: "", sku: "" }

describe("useUrlFilters setFilters", () => {
  it("skips router.replace when the patch would produce the exact same URL (the mount-effect no-op case)", () => {
    // Reproduces Orders/Payments' `useEffect(() => setFilters({ q:
    // debounced }), [debounced])` firing once on mount with the value
    // the URL already has -- e.g. empty search, no page param.
    searchParamsString = ""
    replace.mockClear()
    const { result } = renderHook(() => useUrlFilters(FILTER_DEFAULTS))

    result.current.setFilters({ q: "" })

    expect(replace).not.toHaveBeenCalled()
  })

  it("still calls router.replace when the value genuinely differs from the URL", () => {
    searchParamsString = ""
    replace.mockClear()
    const { result } = renderHook(() => useUrlFilters(FILTER_DEFAULTS))

    result.current.setFilters({ q: "shampoo" })

    expect(replace).toHaveBeenCalledWith("/orders?q=shampoo", { scroll: false })
  })

  it("still calls router.replace when a stale page param must be dropped, even if the named field is unchanged", () => {
    // A real change: `page=3` must be removed once any non-page filter
    // is touched, even though `q` itself already matches.
    searchParamsString = "q=shampoo&page=3"
    replace.mockClear()
    const { result } = renderHook(() => useUrlFilters(FILTER_DEFAULTS))

    result.current.setFilters({ q: "shampoo" })

    expect(replace).toHaveBeenCalledWith("/orders?q=shampoo", { scroll: false })
  })

  it("no-op check also covers deleting a value back to its default", () => {
    searchParamsString = ""
    replace.mockClear()
    const { result } = renderHook(() => useUrlFilters(FILTER_DEFAULTS))

    // Setting an already-absent field back to its own default is also a
    // genuine no-op -- e.g. clearing an input that was already empty.
    result.current.setFilters({ sku: "" })

    expect(replace).not.toHaveBeenCalled()
  })
})
