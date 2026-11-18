import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"

// Mirrors app/services/shiprocket_wallet_service.py. Decimals arrive as strings.

export interface WalletState {
  balance: string | null
  fetched_at: string | null
  stale: boolean
  low_balance: boolean
  low_balance_threshold: string
  guard_enabled: boolean
  ledger_sync_enabled: boolean
  last_ledger_sync_at: string | null
}

export interface WalletSummary {
  state: WalletState
  total_recharges: string
  shipping_expenses: string
  cod_charges: string
  rto_expenses: string
  weight_discrepancy_charges: string
  other_debits: string
  credits_and_reversals: string
  net_shipping_cost: string
  pending_count: number
  pending_amount: string
  failed_count: number
  failed_amount: string
  unverified_recharge_claims: number
  opening_balance: string | null
  closing_balance: string | null
  ledger_consistent: boolean | null
  ledger_gap: string | null
}

export interface WalletTxn {
  id: string
  transaction_ref: string | null
  txn_date: string
  txn_type: string
  direction: "debit" | "credit"
  amount: string
  balance_after: string | null
  order_number: string | null
  awb: string | null
  courier_name: string | null
  description: string | null
  payment_reference: string | null
  status: string
  source: string
  manually_verified: boolean
}

export interface WalletTxnFilters {
  q?: string
  date_from?: string
  date_to?: string
  txn_type?: string
  direction?: "debit" | "credit"
  courier?: string
  status?: string
}

export interface PeriodRow {
  period: string
  recharges: string
  shipping_expenses: string
  cod_charges: string
  rto_expenses: string
  weight_discrepancy: string
  credits_and_reversals: string
  net_shipping_cost: string
}

export interface OrderExpenses {
  actual: {
    transactions: WalletTxn[]
    total_debits: string
    total_credits: string
    net_cost: string
  }
  estimated: { checked_at: string; estimated_charge: string | null; balance: string | null; outcome: string }[]
  note: string
}

const BASE = "/shiprocket-wallet"
const KEY = ["shiprocket-wallet"]

function clean<T extends object>(params: T): Record<string, unknown> {
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v !== undefined))
}

export function useWalletSummary(params: { date_from?: string; date_to?: string }) {
  return useQuery({
    queryKey: [...KEY, "summary", params],
    queryFn: async () =>
      (await apiClient.get<ApiResponse<WalletSummary>>(`${BASE}/summary`, { params: clean(params) }))
        .data.data as WalletSummary,
    placeholderData: (previous) => previous,
  })
}

export function useWalletTransactions(filters: WalletTxnFilters & { page: number; pageSize: number }) {
  const { pageSize, ...rest } = filters
  return useQuery({
    queryKey: [...KEY, "transactions", filters],
    queryFn: async () =>
      (
        await apiClient.get<PaginatedResponse<WalletTxn>>(`${BASE}/transactions`, {
          params: { ...clean(rest), page_size: pageSize },
        })
      ).data,
    placeholderData: (previous) => previous,
  })
}

export function useWalletPeriods(by: "month" | "year", params: { date_from?: string; date_to?: string }) {
  return useQuery({
    queryKey: [...KEY, "periods", by, params],
    queryFn: async () =>
      (
        await apiClient.get<ApiResponse<PeriodRow[]>>(`${BASE}/periods`, {
          params: { by, ...clean(params) },
        })
      ).data.data ?? [],
  })
}

export function useOrderExpenses(orderNumber: string, awb: string) {
  return useQuery({
    queryKey: [...KEY, "order", orderNumber, awb],
    enabled: Boolean(orderNumber || awb),
    queryFn: async () =>
      (
        await apiClient.get<ApiResponse<OrderExpenses>>(`${BASE}/order-expenses`, {
          params: clean({ order_number: orderNumber, awb }),
        })
      ).data.data,
  })
}

export function useRefreshWallet() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      (await apiClient.post<ApiResponse<WalletState>>(`${BASE}/refresh`)).data.data,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })
}

export function useSyncWalletLedger() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () =>
      (
        await apiClient.post<
          ApiResponse<{ created: number; updated: number; skipped: number; unparsed: number }>
        >(`${BASE}/sync`)
      ).data.data,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })
}

export function useCreateRechargeClaim() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: { amount: string; payment_reference: string; note?: string }) =>
      (await apiClient.post<ApiResponse<WalletTxn>>(`${BASE}/recharge-claims`, body)).data.data,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })
}

export function useVerifyRechargeClaim() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) =>
      (await apiClient.post<ApiResponse<WalletTxn>>(`${BASE}/recharge-claims/${id}/verify`)).data
        .data,
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  })
}

export function useExportWalletTransactions() {
  return useMutation({
    mutationFn: async (filters: WalletTxnFilters) => {
      const response = await apiClient.get(`${BASE}/transactions/export`, {
        params: clean(filters),
        responseType: "blob",
      })
      const url = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement("a")
      link.href = url
      link.download = "shiprocket-wallet-transactions.xlsx"
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    },
  })
}
