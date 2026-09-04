import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  AssignCheckoutsInput,
  AssignedCheckout,
  AssignedOrder,
  AssignOrdersInput,
  CallAttempt,
  CheckoutAssignment,
  CheckoutCallAttempt,
  LeadCategory,
  OrderAssignment,
  ReassignCheckoutInput,
  ReassignOrderInput,
  TelecallerOption,
  TelecallerPerformance,
  TelecallingSummary,
} from "@/types/telecalling"

interface UnfulfilledOrdersParams {
  page: number
  pageSize: number
  call_status?: string
  telecaller_id?: string
  date_from?: string
  date_to?: string
}

async function fetchUnfulfilledTeamOrders(
  params: UnfulfilledOrdersParams
): Promise<PaginatedResponse<AssignedOrder>> {
  const response = await apiClient.get<PaginatedResponse<AssignedOrder>>(
    "/team/orders/unfulfilled",
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        call_status: params.call_status || undefined,
        telecaller_id: params.telecaller_id || undefined,
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
      },
    }
  )
  return response.data
}

/** The Team Leader's "Unfulfilled Orders" browsing surface — includes
 * both unassigned (available to grab) and already-assigned-within-team
 * orders, per `GET /team/orders/unfulfilled`'s pool semantics.
 */
export function useUnfulfilledTeamOrders(params: UnfulfilledOrdersParams) {
  return useQuery({
    queryKey: ["team", "orders", "unfulfilled", params],
    queryFn: () => fetchUnfulfilledTeamOrders(params),
    placeholderData: (previous) => previous,
  })
}

interface LeadPoolParams {
  page: number
  pageSize: number
  category?: LeadCategory
  call_status?: string
  date_from?: string
  date_to?: string
}

async function fetchLeadPool(
  params: LeadPoolParams
): Promise<PaginatedResponse<AssignedOrder>> {
  const response = await apiClient.get<PaginatedResponse<AssignedOrder>>("/team/leads", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      category: params.category || undefined,
      call_status: params.call_status || undefined,
      date_from: params.date_from || undefined,
      date_to: params.date_to || undefined,
    },
  })
  return response.data
}

/** The widened Admin/Manager Lead Pool — COD Unfulfilled / COD Fulfilled /
 * Prepaid orders, filterable by `category`. Distinct from
 * `useUnfulfilledTeamOrders` (which stays all-unfulfilled-regardless-of-
 * payment-type, unchanged).
 */
export function useLeadPool(params: LeadPoolParams) {
  return useQuery({
    queryKey: ["team", "leads", params],
    queryFn: () => fetchLeadPool(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchTeamOrder(orderId: string): Promise<AssignedOrder> {
  const response = await apiClient.get<ApiResponse<AssignedOrder>>(
    `/team/orders/${orderId}`
  )
  if (!response.data.data) throw new Error("Order not found.")
  return response.data.data
}

export function useTeamOrder(orderId: string) {
  return useQuery({
    queryKey: ["team", "orders", orderId],
    queryFn: () => fetchTeamOrder(orderId),
    enabled: Boolean(orderId),
  })
}

async function fetchTeamOrderCallHistory(orderId: string): Promise<CallAttempt[]> {
  const response = await apiClient.get<ApiResponse<CallAttempt[]>>(
    `/team/orders/${orderId}/calls`
  )
  return response.data.data ?? []
}

/** Read-only for a Team Leader reviewing one of their team's orders. */
export function useTeamOrderCallHistory(orderId: string) {
  return useQuery({
    queryKey: ["team", "orders", orderId, "calls"],
    queryFn: () => fetchTeamOrderCallHistory(orderId),
    enabled: Boolean(orderId),
  })
}

async function fetchTeamTelecallers(): Promise<TelecallerPerformance[]> {
  const response =
    await apiClient.get<ApiResponse<TelecallerPerformance[]>>("/team/telecallers")
  return response.data.data ?? []
}

export function useTeamTelecallers() {
  return useQuery({
    queryKey: ["team", "telecallers"],
    queryFn: fetchTeamTelecallers,
  })
}

async function fetchAssignableTelecallers(): Promise<TelecallerOption[]> {
  const response = await apiClient.get<ApiResponse<TelecallerOption[]>>(
    "/team/telecallers/roster"
  )
  return response.data.data ?? []
}

/** The "Select Telecaller" assignment-dialog roster — every active
 * TELECALLER-role user in scope (Administration -> Users), regardless of
 * whether they already have any lead assigned. Deliberately NOT
 * `useTeamTelecallers` above: that endpoint's per-telecaller counts only
 * ever include telecallers with existing assignment activity, so a
 * brand-new Telecaller would never appear in a "Select Telecaller"
 * dropdown backed by it.
 */
export function useAssignableTelecallers() {
  return useQuery({
    queryKey: ["team", "telecallers", "roster"],
    queryFn: fetchAssignableTelecallers,
  })
}

interface TelecallerOrdersParams {
  page: number
  pageSize: number
  call_status?: string
}

async function fetchTelecallerOrders(
  telecallerId: string,
  params: TelecallerOrdersParams
): Promise<PaginatedResponse<AssignedOrder>> {
  const response = await apiClient.get<PaginatedResponse<AssignedOrder>>(
    `/team/telecallers/${telecallerId}/orders`,
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        call_status: params.call_status || undefined,
      },
    }
  )
  return response.data
}

export function useTelecallerOrders(
  telecallerId: string,
  params: TelecallerOrdersParams
) {
  return useQuery({
    queryKey: ["team", "telecallers", telecallerId, "orders", params],
    queryFn: () => fetchTelecallerOrders(telecallerId, params),
    enabled: Boolean(telecallerId),
    placeholderData: (previous) => previous,
  })
}

async function fetchTeamSummary(): Promise<TelecallingSummary> {
  const response = await apiClient.get<ApiResponse<TelecallingSummary>>("/team/summary")
  if (!response.data.data) throw new Error("Summary not available.")
  return response.data.data
}

export function useTeamSummary() {
  return useQuery({
    queryKey: ["team", "summary"],
    queryFn: fetchTeamSummary,
  })
}

/** Bulk (or single-order) assignment — Manual mode assigns every
 * `order_ids` entry to one `telecaller_id`; Equal mode round-robins
 * across `telecaller_ids`, matching the backend's exact distribution.
 */
export function useAssignOrders() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: AssignOrdersInput) => {
      const response = await apiClient.post<ApiResponse<OrderAssignment[]>>(
        "/team/orders/assign",
        input
      )
      return response.data.data ?? []
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["team"] })
    },
  })
}

export function useReassignOrder() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: ReassignOrderInput) => {
      const response = await apiClient.post<ApiResponse<OrderAssignment>>(
        "/team/orders/reassign",
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["team"] })
    },
  })
}

interface CheckoutPoolParams {
  page: number
  pageSize: number
  call_status?: string
  date_from?: string
  date_to?: string
}

async function fetchTeamCheckouts(
  params: CheckoutPoolParams
): Promise<PaginatedResponse<AssignedCheckout>> {
  const response = await apiClient.get<PaginatedResponse<AssignedCheckout>>(
    "/team/checkouts",
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

/** The Admin/Manager Abandoned Checkout pool — open, contactable
 * checkouts only (see `CheckoutAssignmentRepository.list_pool`'s
 * docstring backend-side).
 */
export function useTeamCheckouts(params: CheckoutPoolParams) {
  return useQuery({
    queryKey: ["team", "checkouts", params],
    queryFn: () => fetchTeamCheckouts(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchTeamCheckout(checkoutId: string): Promise<AssignedCheckout> {
  const response = await apiClient.get<ApiResponse<AssignedCheckout>>(
    `/team/checkouts/${checkoutId}`
  )
  if (!response.data.data) throw new Error("Checkout not found.")
  return response.data.data
}

export function useTeamCheckout(checkoutId: string) {
  return useQuery({
    queryKey: ["team", "checkouts", checkoutId],
    queryFn: () => fetchTeamCheckout(checkoutId),
    enabled: Boolean(checkoutId),
  })
}

async function fetchTeamCheckoutCallHistory(
  checkoutId: string
): Promise<CheckoutCallAttempt[]> {
  const response = await apiClient.get<ApiResponse<CheckoutCallAttempt[]>>(
    `/team/checkouts/${checkoutId}/calls`
  )
  return response.data.data ?? []
}

export function useTeamCheckoutCallHistory(checkoutId: string) {
  return useQuery({
    queryKey: ["team", "checkouts", checkoutId, "calls"],
    queryFn: () => fetchTeamCheckoutCallHistory(checkoutId),
    enabled: Boolean(checkoutId),
  })
}

async function fetchTelecallerCheckouts(
  telecallerId: string,
  params: CheckoutPoolParams
): Promise<PaginatedResponse<AssignedCheckout>> {
  const response = await apiClient.get<PaginatedResponse<AssignedCheckout>>(
    `/team/telecallers/${telecallerId}/checkouts`,
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        call_status: params.call_status || undefined,
      },
    }
  )
  return response.data
}

export function useTelecallerCheckouts(telecallerId: string, params: CheckoutPoolParams) {
  return useQuery({
    queryKey: ["team", "telecallers", telecallerId, "checkouts", params],
    queryFn: () => fetchTelecallerCheckouts(telecallerId, params),
    enabled: Boolean(telecallerId),
    placeholderData: (previous) => previous,
  })
}

export function useAssignCheckouts() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: AssignCheckoutsInput) => {
      const response = await apiClient.post<ApiResponse<CheckoutAssignment[]>>(
        "/team/checkouts/assign",
        input
      )
      return response.data.data ?? []
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["team"] })
    },
  })
}

export function useReassignCheckout() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: ReassignCheckoutInput) => {
      const response = await apiClient.post<ApiResponse<CheckoutAssignment>>(
        "/team/checkouts/reassign",
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["team"] })
    },
  })
}
