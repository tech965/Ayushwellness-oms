import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { Customer, CustomerListFilters, CustomerSummary } from "@/types/customer"
import type { Order } from "@/types/order"

interface ListParams extends CustomerListFilters {
  page: number
  pageSize: number
}

async function fetchCustomers(params: ListParams): Promise<PaginatedResponse<Customer>> {
  const response = await apiClient.get<PaginatedResponse<Customer>>("/customers", {
    params: { page: params.page, page_size: params.pageSize, q: params.q || undefined },
  })
  return response.data
}

export function useCustomers(params: ListParams) {
  return useQuery({
    queryKey: ["customers", params],
    queryFn: () => fetchCustomers(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchCustomer(id: string): Promise<Customer> {
  const response = await apiClient.get<ApiResponse<Customer>>(`/customers/${id}`)
  if (!response.data.data) throw new Error("Customer not found.")
  return response.data.data
}

export function useCustomer(id: string) {
  return useQuery({
    queryKey: ["customers", id],
    queryFn: () => fetchCustomer(id),
    enabled: Boolean(id),
  })
}

async function fetchCustomerSummary(id: string): Promise<CustomerSummary> {
  const response = await apiClient.get<ApiResponse<CustomerSummary>>(
    `/customers/${id}/summary`
  )
  if (!response.data.data) throw new Error("Customer summary not found.")
  return response.data.data
}

export function useCustomerSummary(id: string) {
  return useQuery({
    queryKey: ["customers", id, "summary"],
    queryFn: () => fetchCustomerSummary(id),
    enabled: Boolean(id),
  })
}

async function fetchCustomerOrders(
  id: string,
  page: number,
  pageSize: number
): Promise<PaginatedResponse<Order>> {
  const response = await apiClient.get<PaginatedResponse<Order>>(
    `/customers/${id}/orders`,
    {
      params: { page, page_size: pageSize },
    }
  )
  return response.data
}

export function useCustomerOrders(id: string, page: number, pageSize: number) {
  return useQuery({
    queryKey: ["customers", id, "orders", page, pageSize],
    queryFn: () => fetchCustomerOrders(id, page, pageSize),
    enabled: Boolean(id),
    placeholderData: (previous) => previous,
  })
}

export interface CreateCustomerInput {
  first_name?: string
  last_name?: string
  full_name?: string
  email?: string
  phone?: string
  alternate_phone?: string
  notes?: string
}

export function useCreateCustomer() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: CreateCustomerInput) => {
      const response = await apiClient.post<ApiResponse<Customer>>("/customers", input)
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["customers"] })
    },
  })
}

export interface UpdateCustomerInput {
  first_name?: string
  last_name?: string
  full_name?: string
  email?: string
  phone?: string
  alternate_phone?: string
  notes?: string
  is_active?: boolean
}

export function useUpdateCustomer(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: UpdateCustomerInput) => {
      const response = await apiClient.patch<ApiResponse<Customer>>(
        `/customers/${id}`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["customers", id] })
    },
  })
}
