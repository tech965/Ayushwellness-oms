import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import axios from "axios"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { CashfreeCheckout, CashfreePaymentStatus } from "@/types/cashfree"

async function fetchCashfreePayment(orderId: string): Promise<CashfreePaymentStatus | null> {
  try {
    const response = await apiClient.get<ApiResponse<CashfreePaymentStatus>>(
      `/payments/cashfree/orders/${orderId}`
    )
    return response.data.data
  } catch (error) {
    // No Cashfree checkout has been started for this order yet -- an
    // ordinary, expected state, not an error to surface to the user.
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return null
    }
    throw error
  }
}

export function useCashfreePayment(orderId: string) {
  return useQuery({
    queryKey: ["cashfree-payment", "order", orderId],
    queryFn: () => fetchCashfreePayment(orderId),
    enabled: Boolean(orderId),
  })
}

export function useCreateCashfreeCheckout(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      // Deliberately no request body -- the amount is always computed
      // server-side from the OMS order; the frontend never supplies or
      // overrides it.
      const response = await apiClient.post<ApiResponse<CashfreeCheckout>>(
        `/payments/cashfree/orders/${orderId}/create`,
        {}
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["cashfree-payment", "order", orderId] })
    },
  })
}

export function useReconcileCashfreePayment(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<ApiResponse<CashfreePaymentStatus>>(
        `/payments/cashfree/orders/${orderId}/reconcile`,
        {}
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["cashfree-payment", "order", orderId] })
      void queryClient.invalidateQueries({ queryKey: ["orders", orderId] })
    },
  })
}
