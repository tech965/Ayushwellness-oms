import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  AssignedCheckout,
  AssignedOrder,
  BulkConfirmOrdersResponse,
  CallAttempt,
  CallHistoryEntry,
  CheckoutAssignment,
  CheckoutCallAttempt,
  EditCallAttemptInput,
  LogCallInput,
  OrderAssignment,
  TelecallingSummary,
} from "@/types/telecalling"
import type { OrderDetail } from "@/types/order"

interface MyOrdersParams {
  page: number
  pageSize: number
  call_status?: string
  date_from?: string
  date_to?: string
}

async function fetchMyOrders(
  params: MyOrdersParams
): Promise<PaginatedResponse<AssignedOrder>> {
  const response = await apiClient.get<PaginatedResponse<AssignedOrder>>(
    "/telecaller/orders",
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        call_status: params.call_status || undefined,
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
      },
    }
  )
  return response.data
}

/** Hard-scoped server-side to the logged-in telecaller — there is no
 * client-supplied telecaller id anywhere in this request.
 */
export function useMyOrders(params: MyOrdersParams) {
  return useQuery({
    queryKey: ["telecaller", "orders", params],
    queryFn: () => fetchMyOrders(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchMyOrder(orderId: string): Promise<AssignedOrder> {
  const response = await apiClient.get<ApiResponse<AssignedOrder>>(
    `/telecaller/orders/${orderId}`
  )
  if (!response.data.data) throw new Error("Order not found.")
  return response.data.data
}

export function useMyOrder(orderId: string) {
  return useQuery({
    queryKey: ["telecaller", "orders", orderId],
    queryFn: () => fetchMyOrder(orderId),
    enabled: Boolean(orderId),
  })
}

async function fetchCallHistory(orderId: string): Promise<CallAttempt[]> {
  const response = await apiClient.get<ApiResponse<CallAttempt[]>>(
    `/telecaller/orders/${orderId}/calls`
  )
  return response.data.data ?? []
}

export function useCallHistory(orderId: string) {
  return useQuery({
    queryKey: ["telecaller", "orders", orderId, "calls"],
    queryFn: () => fetchCallHistory(orderId),
    enabled: Boolean(orderId),
  })
}

export function useLogCall(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: LogCallInput) => {
      const response = await apiClient.post<ApiResponse<CallAttempt>>(
        `/telecaller/orders/${orderId}/calls`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      // Broad enough to match the "My Assigned Orders" list
      // (["telecaller","orders",params]) as well as this one order's own
      // detail (["telecaller","orders",orderId]) and call history
      // (["telecaller","orders",orderId,"calls"]) -- all three share this
      // prefix. Invalidating only ["telecaller","orders",orderId] (the
      // previous behavior) never matched the list query: its own key's
      // third element is a params *object*, not this order's id, so
      // TanStack's partial-match check silently skipped it and the list
      // kept showing stale status/attempt data until its 30s staleTime
      // happened to expire.
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "orders"] })
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "follow-ups"] })
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "summary"] })
    },
  })
}

export function useEditCallAttempt(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({
      attemptId,
      input,
    }: {
      attemptId: string
      input: EditCallAttemptInput
    }) => {
      const response = await apiClient.patch<ApiResponse<CallAttempt>>(
        `/telecaller/orders/${orderId}/calls/${attemptId}`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      // See `useLogCall`'s identical comment above — an edit can change
      // `current_status`/`attempt_count`, so the order detail and list
      // queries need invalidating too, not just call history.
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "orders"] })
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "follow-ups"] })
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "summary"] })
    },
  })
}

export function useDeleteCallAttempt(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (attemptId: string) => {
      await apiClient.delete<ApiResponse<null>>(
        `/telecaller/orders/${orderId}/calls/${attemptId}`
      )
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "orders"] })
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "follow-ups"] })
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "summary"] })
    },
  })
}

/** PENDING -> CONFIRMED only (`Order.status`) -- never touches call
 * outcome/fulfillment/shipment status. Distinct from `useLogCall`.
 */
export function useConfirmOrder(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<ApiResponse<OrderDetail>>(
        `/telecaller/orders/${orderId}/confirm`
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "orders"] })
      // A newly-confirmed order is immediately eligible for the
      // Fulfillment Queue/Dashboard -- invalidate those too so a
      // Fulfillment user who already has one of those pages open (same
      // tab an admin/superuser tests both roles from, or a page left open
      // across a shift) sees it without a hard refresh. Cross-tab/
      // cross-login staleness still needs `refetchOnWindowFocus` on the
      // fulfillment-side queries themselves (see services/shipment-queue.ts)
      // since each browser tab holds its own QueryClient.
      void queryClient.invalidateQueries({ queryKey: ["shipment-queue"] })
      void queryClient.invalidateQueries({ queryKey: ["shipments"] })
    },
  })
}

/** CONFIRMED -> PENDING only -- the exact inverse of `useConfirmOrder`,
 * for undoing a mistaken confirmation. The backend blocks this (409) once
 * a shipment already exists for the order.
 */
export function useUnconfirmOrder(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<ApiResponse<OrderDetail>>(
        `/telecaller/orders/${orderId}/unconfirm`
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "orders"] })
      void queryClient.invalidateQueries({ queryKey: ["shipment-queue"] })
      void queryClient.invalidateQueries({ queryKey: ["shipments"] })
    },
  })
}

export interface UpdateOrderAddressInput {
  contact_name?: string
  contact_phone?: string
  line1: string
  line2?: string
  city: string
  state?: string
  pin_code: string
  country?: string
}

/** Updates `Order.shipping_address` and pushes it to Shopify. Always
 * resolves on a 200 (the OMS write succeeding) -- the caller must check
 * `shipping_address_sync_status` on the returned order to tell "saved
 * and synced" apart from "saved, Shopify sync failed" rather than
 * assuming success from the mutation not throwing. Saving again (even
 * unchanged) is itself the retry for a failed Shopify push -- there's no
 * separate retry endpoint.
 */
export function useUpdateOrderAddress(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: UpdateOrderAddressInput) => {
      const response = await apiClient.patch<ApiResponse<AssignedOrder>>(
        `/telecaller/orders/${orderId}/address`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "orders"] })
    },
  })
}

/** Confirms every selected order independently -- the response always
 * reports a per-order result, never an all-or-nothing failure.
 */
export function useBulkConfirmOrders() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (orderIds: string[]) => {
      const response = await apiClient.post<ApiResponse<BulkConfirmOrdersResponse>>(
        "/telecaller/orders/confirm",
        { order_ids: orderIds }
      )
      if (!response.data.data) throw new Error("Bulk confirm did not return a result.")
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "orders"] })
      void queryClient.invalidateQueries({ queryKey: ["shipment-queue"] })
      void queryClient.invalidateQueries({ queryKey: ["shipments"] })
    },
  })
}

export function useScheduleFollowUp(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (nextFollowUpAt: string) => {
      const response = await apiClient.post<ApiResponse<OrderAssignment>>(
        `/telecaller/orders/${orderId}/follow-up`,
        { next_follow_up_at: nextFollowUpAt }
      )
      return response.data.data
    },
    onSuccess: () => {
      // See `useLogCall`'s identical comment above.
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "orders"] })
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "follow-ups"] })
    },
  })
}

interface FollowUpsParams {
  when: "today" | "overdue" | "upcoming"
  page: number
  pageSize: number
}

async function fetchMyFollowUps(
  params: FollowUpsParams
): Promise<PaginatedResponse<AssignedOrder>> {
  const response = await apiClient.get<PaginatedResponse<AssignedOrder>>(
    "/telecaller/follow-ups",
    { params: { when: params.when, page: params.page, page_size: params.pageSize } }
  )
  return response.data
}

export function useMyFollowUps(params: FollowUpsParams) {
  return useQuery({
    queryKey: ["telecaller", "follow-ups", params],
    queryFn: () => fetchMyFollowUps(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchMySummary(): Promise<TelecallingSummary> {
  const response =
    await apiClient.get<ApiResponse<TelecallingSummary>>("/telecaller/summary")
  if (!response.data.data) throw new Error("Summary not available.")
  return response.data.data
}

export function useMySummary() {
  return useQuery({
    queryKey: ["telecaller", "summary"],
    queryFn: fetchMySummary,
  })
}

async function fetchMyCallHistory(): Promise<CallHistoryEntry[]> {
  const response =
    await apiClient.get<ApiResponse<CallHistoryEntry[]>>("/telecaller/calls")
  return response.data.data ?? []
}

/** Every call the logged-in telecaller has made, across every order —
 * the "Call History" nav page.
 */
export function useMyCallHistory() {
  return useQuery({
    queryKey: ["telecaller", "calls"],
    queryFn: fetchMyCallHistory,
  })
}

interface MyCheckoutsParams {
  page: number
  pageSize: number
  call_status?: string
  date_from?: string
  date_to?: string
}

async function fetchMyCheckouts(
  params: MyCheckoutsParams
): Promise<PaginatedResponse<AssignedCheckout>> {
  const response = await apiClient.get<PaginatedResponse<AssignedCheckout>>(
    "/telecaller/checkouts",
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        call_status: params.call_status || undefined,
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
      },
    }
  )
  return response.data
}

export function useMyCheckouts(params: MyCheckoutsParams) {
  return useQuery({
    queryKey: ["telecaller", "checkouts", params],
    queryFn: () => fetchMyCheckouts(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchMyCheckout(checkoutId: string): Promise<AssignedCheckout> {
  const response = await apiClient.get<ApiResponse<AssignedCheckout>>(
    `/telecaller/checkouts/${checkoutId}`
  )
  if (!response.data.data) throw new Error("Checkout not found.")
  return response.data.data
}

export function useMyCheckout(checkoutId: string) {
  return useQuery({
    queryKey: ["telecaller", "checkouts", checkoutId],
    queryFn: () => fetchMyCheckout(checkoutId),
    enabled: Boolean(checkoutId),
  })
}

async function fetchCheckoutCallHistory(
  checkoutId: string
): Promise<CheckoutCallAttempt[]> {
  const response = await apiClient.get<ApiResponse<CheckoutCallAttempt[]>>(
    `/telecaller/checkouts/${checkoutId}/calls`
  )
  return response.data.data ?? []
}

export function useCheckoutCallHistory(checkoutId: string) {
  return useQuery({
    queryKey: ["telecaller", "checkouts", checkoutId, "calls"],
    queryFn: () => fetchCheckoutCallHistory(checkoutId),
    enabled: Boolean(checkoutId),
  })
}

export function useLogCheckoutCall(checkoutId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: LogCallInput) => {
      const response = await apiClient.post<ApiResponse<CheckoutCallAttempt>>(
        `/telecaller/checkouts/${checkoutId}/calls`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      // See `useLogCall`'s identical comment in this file — the list
      // query's key is ["telecaller","checkouts",params], which
      // ["telecaller","checkouts",checkoutId] never partial-matched.
      void queryClient.invalidateQueries({
        queryKey: ["telecaller", "checkouts"],
      })
      void queryClient.invalidateQueries({
        queryKey: ["telecaller", "checkout-follow-ups"],
      })
      void queryClient.invalidateQueries({ queryKey: ["telecaller", "summary"] })
    },
  })
}

export function useScheduleCheckoutFollowUp(checkoutId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (nextFollowUpAt: string) => {
      const response = await apiClient.post<ApiResponse<CheckoutAssignment>>(
        `/telecaller/checkouts/${checkoutId}/follow-up`,
        { next_follow_up_at: nextFollowUpAt }
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["telecaller", "checkouts"],
      })
      void queryClient.invalidateQueries({
        queryKey: ["telecaller", "checkout-follow-ups"],
      })
    },
  })
}

interface CheckoutFollowUpsParams {
  when: "today" | "overdue" | "upcoming"
  page: number
  pageSize: number
}

async function fetchMyCheckoutFollowUps(
  params: CheckoutFollowUpsParams
): Promise<PaginatedResponse<AssignedCheckout>> {
  const response = await apiClient.get<PaginatedResponse<AssignedCheckout>>(
    "/telecaller/checkout-follow-ups",
    { params: { when: params.when, page: params.page, page_size: params.pageSize } }
  )
  return response.data
}

export function useMyCheckoutFollowUps(params: CheckoutFollowUpsParams) {
  return useQuery({
    queryKey: ["telecaller", "checkout-follow-ups", params],
    queryFn: () => fetchMyCheckoutFollowUps(params),
    placeholderData: (previous) => previous,
  })
}
