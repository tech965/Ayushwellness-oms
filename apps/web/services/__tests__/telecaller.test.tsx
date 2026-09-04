import { describe, expect, it, vi, beforeEach } from "vitest"
import { renderHook, waitFor, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"

import { apiClient } from "@/lib/api-client"
import { useLogCall, useLogCheckoutCall, useMyCheckouts, useMyOrders } from "@/services/telecaller"

vi.mock("@/lib/api-client", () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
}))

const mockedGet = vi.mocked(apiClient.get)
const mockedPost = vi.mocked(apiClient.post)

function listPage(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      success: true,
      message: "Success",
      data: [
        {
          order_id: "order-1",
          order_number: "OMS-1",
          customer_name: "Test Customer",
          customer_phone: null,
          item_summary: null,
          total_amount: "100.00",
          payment_type: "prepaid",
          payment_status: "pending",
          fulfillment_status: "unfulfilled",
          order_datetime: "2026-01-01T00:00:00Z",
          shipping_address: null,
          assignment_id: "assign-1",
          assigned_to: "tc-1",
          assigned_to_name: "Komal",
          call_status: "not_called",
          attempt_count: 0,
          last_attempt_at: null,
          next_follow_up_at: null,
          lead_category: null,
          priority: null,
          ...overrides,
        },
      ],
      meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
    },
  }
}

function checkoutPage(overrides: Record<string, unknown> = {}) {
  return {
    data: {
      success: true,
      message: "Success",
      data: [
        {
          checkout_id: "checkout-1",
          customer_name: "Test Customer",
          customer_phone: null,
          customer_email: null,
          item_summary: null,
          total_amount: "50.00",
          checkout_url: null,
          checkout_created_at: null,
          is_recovered: false,
          assignment_id: "assign-2",
          assigned_to: "tc-1",
          assigned_to_name: "Komal",
          call_status: "not_called",
          attempt_count: 0,
          last_attempt_at: null,
          next_follow_up_at: null,
          lead_category: "abandoned_checkout",
          priority: null,
          ...overrides,
        },
      ],
      meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
    },
  }
}

interface ListRow {
  call_status: string
  attempt_count: number
}
interface ListCache {
  data: ListRow[]
}

/**
 * Regression for the reported bug: after logging a call, "My Assigned
 * Orders" (and its checkout-lead counterpart) must actually refetch — not
 * just the one record's own detail/call-history queries. Uses the real
 * hooks against a real `QueryClient` (only the HTTP layer is mocked), and
 * asserts against the QueryClient's own cache — the authoritative record of
 * which queries invalidation actually reached — so this proves the genuine
 * cache-invalidation behavior rather than a mocked stand-in for it.
 */
describe("telecaller call-log cache invalidation", () => {
  beforeEach(() => {
    mockedGet.mockReset()
    mockedPost.mockReset()
  })

  function wrapper(queryClient: QueryClient) {
    return function Wrapper({ children }: { children: ReactNode }) {
      return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    }
  }

  it("refetches the assigned-orders list after logging a call, not just the order's own detail", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const listQueryKey = ["telecaller", "orders", { page: 1, pageSize: 20 }]

    mockedGet.mockImplementation((url: string) => {
      if (url === "/telecaller/orders") return Promise.resolve(listPage())
      throw new Error(`unexpected GET ${url}`)
    })
    mockedPost.mockResolvedValue({
      data: { success: true, message: "Call logged.", data: { id: "attempt-1" } },
    })

    const { result: listResult } = renderHook(
      () => useMyOrders({ page: 1, pageSize: 20 }),
      { wrapper: wrapper(queryClient) }
    )
    const { result: mutationResult } = renderHook(() => useLogCall("order-1"), {
      wrapper: wrapper(queryClient),
    })

    await waitFor(() => expect(listResult.current.isSuccess).toBe(true))
    expect(mockedGet).toHaveBeenCalledTimes(1)
    expect(queryClient.getQueryData<ListCache>(listQueryKey)?.data[0].call_status).toBe(
      "not_called"
    )

    // After logging, the list endpoint should be hit again with fresh data.
    mockedGet.mockImplementation((url: string) => {
      if (url === "/telecaller/orders") {
        return Promise.resolve(listPage({ call_status: "connected", attempt_count: 1 }))
      }
      throw new Error(`unexpected GET ${url}`)
    })

    await act(async () => {
      await mutationResult.current.mutateAsync({ outcome: "connected", notes: "Reached customer." })
    })

    // The core assertion: the list query's own cache entry was actually
    // refetched with fresh data -- proving invalidation reached it, not
    // just the order's own detail/call-history queries (the previous
    // narrower `["telecaller","orders",orderId]` invalidation never
    // matched this list query's key, `["telecaller","orders",params]`).
    await waitFor(() => {
      const cached = queryClient.getQueryData<ListCache>(listQueryKey)
      expect(cached?.data[0].call_status).toBe("connected")
      expect(cached?.data[0].attempt_count).toBe(1)
    })
  })

  it("does the same for checkout leads (useLogCheckoutCall refetches useMyCheckouts)", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const listQueryKey = ["telecaller", "checkouts", { page: 1, pageSize: 20 }]

    mockedGet.mockImplementation((url: string) => {
      if (url === "/telecaller/checkouts") return Promise.resolve(checkoutPage())
      throw new Error(`unexpected GET ${url}`)
    })
    mockedPost.mockResolvedValue({
      data: { success: true, message: "Call logged.", data: { id: "attempt-2" } },
    })

    const { result: listResult } = renderHook(
      () => useMyCheckouts({ page: 1, pageSize: 20 }),
      { wrapper: wrapper(queryClient) }
    )
    const { result: mutationResult } = renderHook(() => useLogCheckoutCall("checkout-1"), {
      wrapper: wrapper(queryClient),
    })

    await waitFor(() => expect(listResult.current.isSuccess).toBe(true))
    expect(queryClient.getQueryData<ListCache>(listQueryKey)?.data[0].call_status).toBe(
      "not_called"
    )

    mockedGet.mockImplementation((url: string) => {
      if (url === "/telecaller/checkouts") {
        return Promise.resolve(checkoutPage({ call_status: "connected", attempt_count: 1 }))
      }
      throw new Error(`unexpected GET ${url}`)
    })

    await act(async () => {
      await mutationResult.current.mutateAsync({ outcome: "connected" })
    })

    await waitFor(() => {
      const cached = queryClient.getQueryData<ListCache>(listQueryKey)
      expect(cached?.data[0].call_status).toBe("connected")
      expect(cached?.data[0].attempt_count).toBe(1)
    })
  })
})
