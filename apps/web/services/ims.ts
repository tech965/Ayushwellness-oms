import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type {
  Adjustment,
  CatalogPage,
  CatalogProduct,
  CatalogVariantHit,
  CategoriesOverview,
  Dashboard,
  ImsItem,
  ImsItemDetail,
  ImsWarehouse,
  LedgerRow,
  Paged,
  PackagingProduct,
  Party,
  ReportData,
  StockOverview,
  StockReturn,
  Transfer,
} from "@/types/ims"

type Params = Record<string, string | number | boolean | undefined | null>

function clean(params?: Params) {
  const out: Record<string, string | number | boolean> = {}
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") out[k] = v
  }
  return out
}

async function get<T>(url: string, params?: Params): Promise<T> {
  const res = await apiClient.get<ApiResponse<T>>(`/ims${url}`, { params: clean(params) })
  if (res.data.data === null) throw new Error("Empty response")
  return res.data.data
}

async function send<T>(method: "post" | "patch" | "put", url: string, body?: unknown): Promise<T> {
  const res = await apiClient.request<ApiResponse<T>>({ method, url: `/ims${url}`, data: body })
  return res.data.data as T
}

/** A new random key per form instance: retries/double-clicks replay the same
 * server record instead of applying the stock change twice. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID().replace(/-/g, "")
}

/** Every IMS mutation invalidates all IMS queries so stock is reconciled with
 * the server everywhere (no optimistic stock edits). */
function useImsMutation<TIn, TOut>(fn: (input: TIn) => Promise<TOut>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ims"] }),
  })
}

export function useDashboard(params: Params) {
  return useQuery({ queryKey: ["ims", "dashboard", params], queryFn: () => get<Dashboard>("/dashboard", params) })
}

export function useWarehouses(activeOnly = false) {
  return useQuery({
    queryKey: ["ims", "warehouses", activeOnly],
    queryFn: () => get<{ items: ImsWarehouse[] }>("/warehouses", { active_only: activeOnly }).then((d) => d.items),
  })
}
export const useCreateWarehouse = () =>
  useImsMutation((b: Partial<ImsWarehouse>) => send<ImsWarehouse>("post", "/warehouses", b))
export const useUpdateWarehouse = () =>
  useImsMutation(({ id, ...b }: Partial<ImsWarehouse> & { id: string }) =>
    send<ImsWarehouse>("patch", `/warehouses/${id}`, b)
  )

export function useCatalogProducts(params: Params) {
  return useQuery({
    queryKey: ["ims", "catalog", "products", params],
    queryFn: () => get<CatalogPage>("/catalog/products", params),
    placeholderData: (p) => p,
  })
}
export function useCatalogProduct(id: string) {
  return useQuery({
    queryKey: ["ims", "catalog", "product", id],
    queryFn: () => get<CatalogProduct>(`/catalog/products/${id}`),
  })
}
/** Categories page data: each bucket category's products and actual
 * per-warehouse stock, for the inline accordion. */
export function useCategoriesOverview() {
  return useQuery({
    queryKey: ["ims", "categories"],
    queryFn: () => get<CategoriesOverview>("/categories"),
  })
}

export function useCatalogCategories() {
  return useQuery({
    queryKey: ["ims", "catalog", "categories"],
    queryFn: () =>
      get<{ items: { category: string; products: number; active_products: number; skus: number }[] }>(
        "/catalog/categories"
      ).then((d) => d.items),
  })
}
/** Category options for filters; resilient to Shopify being unreachable. */
export function useCategoryOptions() {
  return useQuery({
    queryKey: ["ims", "filters", "categories"],
    queryFn: () => get<{ items: string[] }>("/filters/categories").then((d) => d.items),
  })
}
export function useCatalogVariantSearch(q: string, enabled = true, warehouseId?: string) {
  return useQuery({
    queryKey: ["ims", "catalog", "variants", q, warehouseId],
    queryFn: () =>
      get<{ items: CatalogVariantHit[] }>("/catalog/variants", { q, limit: 50, warehouse_id: warehouseId }).then(
        (d) => d.items
      ),
    enabled,
  })
}
export const useRefreshCatalog = () =>
  useImsMutation(() =>
    send<{ products: number; labels_updated: number; registered_items: number; not_found_in_shopify: number; truncated: boolean }>(
      "post",
      "/catalog/refresh"
    )
  )
export const resolveFinishedItem = (product_id: string, variant_id: string) =>
  send<ImsItem>("post", "/items/finished", { product_id, variant_id })

export function useItemSearch(q: string, kind: string | undefined, warehouseId?: string, enabled = true) {
  return useQuery({
    queryKey: ["ims", "items", q, kind, warehouseId],
    queryFn: () =>
      get<{ items: ImsItem[] }>("/items", { q, kind, warehouse_id: warehouseId }).then((d) => d.items),
    enabled,
  })
}
export function useItemDetail(id: string | undefined) {
  return useQuery({
    queryKey: ["ims", "item", id],
    queryFn: () => get<ImsItemDetail>(`/items/${id}`),
    enabled: Boolean(id),
  })
}
export const useUpdateItem = () =>
  useImsMutation(({ id, ...b }: { id: string; low_stock_threshold?: number | null; is_active?: boolean }) =>
    send<ImsItem>("patch", `/items/${id}`, b)
  )

export function useStock(params: Params) {
  return useQuery({
    queryKey: ["ims", "stock", params],
    queryFn: () => get<StockOverview>("/stock", params),
    placeholderData: (p) => p,
  })
}

export function usePackaging(warehouseId?: string, q?: string) {
  return useQuery({
    queryKey: ["ims", "packaging", warehouseId, q],
    queryFn: () => get<{ items: PackagingProduct[]; units: string[] }>("/packaging", { warehouse_id: warehouseId, q }),
  })
}
export const useCreatePackaging = () =>
  useImsMutation((b: { name: string; category?: string; units: string[] }) =>
    send<PackagingProduct>("post", "/packaging", b)
  )
export const useUpdatePackaging = () =>
  useImsMutation(({ id, ...b }: { id: string; add_units?: string[]; name?: string; is_active?: boolean }) =>
    send<PackagingProduct>("patch", `/packaging/${id}`, b)
  )
export const useSetConversions = () =>
  useImsMutation(({ id, rules }: { id: string; rules: { larger_unit: string; smaller_unit: string; ratio: number }[] }) =>
    send<PackagingProduct>("put", `/packaging/${id}/conversions`, { rules })
  )
export const convertUnits = (b: { packaging_product_id: string; from_unit: string; to_unit: string; quantity: number }) =>
  send<{ result: number; is_whole_number: boolean; whole_result: number; remainder_in_from_unit: number; note: string }>(
    "post",
    "/packaging/convert",
    b
  )

export const useStockInward = () =>
  useImsMutation((b: Record<string, unknown>) => send<Adjustment>("post", "/stock/inward", b))
export const useAdjust = () =>
  useImsMutation((b: Record<string, unknown>) => send<Adjustment>("post", "/stock/adjustments", b))
export const useOpeningBalances = () =>
  useImsMutation((b: Record<string, unknown>) => send<{ reference_no: string; lines: number }>("post", "/stock/opening-balances", b))
export function useAdjustments(params: Params) {
  return useQuery({
    queryKey: ["ims", "adjustments", params],
    queryFn: () => get<Paged<Adjustment>>("/stock/adjustments", params),
    placeholderData: (p) => p,
  })
}

export const useCreateTransfer = () =>
  useImsMutation((b: Record<string, unknown>) => send<Transfer>("post", "/transfers", b))
export function useTransfers(params: Params) {
  return useQuery({
    queryKey: ["ims", "transfers", params],
    queryFn: () => get<Paged<Transfer>>("/transfers", params),
    placeholderData: (p) => p,
  })
}

export function useTransferDetail(id: string) {
  return useQuery({ queryKey: ["ims", "transfer", id], queryFn: () => get<Transfer>(`/transfers/${id}`) })
}

export function useParties() {
  return useQuery({
    queryKey: ["ims", "parties"],
    queryFn: () => get<{ items: Party[] }>("/parties").then((d) => d.items),
  })
}
export const useCreateParty = () => useImsMutation((b: { name: string; notes?: string }) => send<Party>("post", "/parties", b))
export const useUpdateParty = () =>
  useImsMutation(({ id, ...b }: { id: string; is_active?: boolean; name?: string }) => send<Party>("patch", `/parties/${id}`, b))
export const useCreateReturn = () =>
  useImsMutation((b: Record<string, unknown>) => send<StockReturn>("post", "/returns", b))
export function useReturns(params: Params) {
  return useQuery({
    queryKey: ["ims", "returns", params],
    queryFn: () => get<Paged<StockReturn>>("/returns", params),
    placeholderData: (p) => p,
  })
}

export function useLedger(params: Params) {
  return useQuery({
    queryKey: ["ims", "ledger", params],
    queryFn: () => get<Paged<LedgerRow>>("/ledger", params),
    placeholderData: (p) => p,
  })
}

export function useReport(name: string, params: Params) {
  return useQuery({
    queryKey: ["ims", "report", name, params],
    queryFn: () => get<ReportData>(`/reports/${name}`, params),
  })
}

export async function downloadReport(name: string, params: Params) {
  const res = await apiClient.get(`/ims/reports/${name}`, {
    params: { ...clean(params), format: "xlsx" },
    responseType: "blob",
  })
  const url = URL.createObjectURL(res.data as Blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `ims-${name}-report.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}
