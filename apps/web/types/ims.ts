export const PACKAGING_UNITS = ["BOX", "OUTER", "SACHETS", "ZIPPER", "BOTTLE", "JAR"] as const
export type PackagingUnit = (typeof PACKAGING_UNITS)[number]

export type StockStatus = "in_stock" | "low_stock" | "out_of_stock"

export interface ImsWarehouse {
  id: string
  code: string
  name: string
  address: string | null
  description: string | null
  is_active: boolean
  sort_order: number
}

export interface WarehouseRef {
  id: string
  name: string | null
  code: string | null
}

export interface ImsItem {
  id: string
  kind: "finished" | "packaging"
  name: string
  sku: string | null
  category: string | null
  unit: PackagingUnit | null
  packaging_product_id: string | null
  shopify_product_id: string | null
  shopify_variant_id: string | null
  is_active: boolean
  low_stock_threshold: number | null
  effective_low_stock_threshold: number | null
  available?: number
}

export interface ImsItemDetail extends ImsItem {
  by_warehouse: (ImsWarehouse & { quantity: number })[]
  total: number
  status: StockStatus
  packaging_product?: PackagingProduct
  sibling_variants?: ImsItem[]
}

export interface StockRow extends ImsItem {
  by_warehouse: Record<string, number>
  total: number
  status: StockStatus
}

export interface StockOverview {
  items: StockRow[]
  total_items: number
  page: number
  page_size: number
  warehouses: ImsWarehouse[]
  totals: { by_warehouse: Record<string, number>; total: number }
}

export interface PackagingUnitCell {
  item_id: string
  quantity: number
  low_stock_threshold: number | null
  is_active: boolean
  by_warehouse: { warehouse_id: string; warehouse: string; code: string; quantity: number }[]
}

export interface ConversionRule {
  larger_unit: PackagingUnit
  smaller_unit: PackagingUnit
  ratio: number
}

export interface PackagingProduct {
  id: string
  name: string
  category: string | null
  shopify_product_id: string | null
  is_active: boolean
  notes: string | null
  tracked_units: PackagingUnit[]
  units: Partial<Record<PackagingUnit, PackagingUnitCell>>
  conversions: ConversionRule[]
  conversion_summary: string[]
}

export interface CatalogVariant {
  variant_id: string
  sku: string | null
  sku_missing: boolean
  title: string | null
  barcode: string | null
  image_url: string | null
  price: string | null
  item_id: string | null
  total_stock: number
  by_warehouse?: (ImsWarehouse & { quantity: number })[]
  status?: StockStatus | "not_tracked"
}

export interface CatalogProduct {
  product_id: string
  title: string
  category: string
  vendor: string | null
  status: string
  image_url: string | null
  variants: CatalogVariant[]
}

export interface CatalogStats {
  active_products: number
  total_products: number
  categories: number
  total_skus: number
  missing_sku_variants: number
}

export interface CatalogPage {
  items: CatalogProduct[]
  total_items: number
  page: number
  page_size: number
  stats: CatalogStats
  fetched_at: number
  truncated: boolean
}

export interface CatalogVariantHit {
  product_id: string
  variant_id: string
  label: string
  sku: string | null
  sku_missing: boolean
  category: string
  image_url: string | null
  barcode: string | null
  item_id: string | null
  total_stock: number
}

export interface Paged<T> {
  items: T[]
  total_items: number
  page: number
  page_size: number
}

export interface TransferLine {
  item: ImsItem
  quantity: number
}

export interface Transfer {
  id: string
  reference_no: string
  source: WarehouseRef
  destination: WarehouseRef
  notes: string | null
  created_by: string | null
  created_at: string
  lines: TransferLine[]
  total_quantity: number
  replayed?: boolean
  movements?: {
    movement_type: string
    warehouse_id: string
    item_id: string
    quantity_delta: number
    quantity_before: number
    quantity_after: number
  }[]
}

export interface Party {
  id: string
  name: string
  notes: string | null
  is_active: boolean
}

export interface StockReturn {
  id: string
  reference_no: string
  warehouse: WarehouseRef
  party: Party | null
  return_date: string
  document_no: string | null
  notes: string | null
  created_by: string | null
  created_at: string
  lines: TransferLine[]
  total_quantity: number
  replayed?: boolean
}

export interface Adjustment {
  id: string
  reference_no: string
  warehouse: WarehouseRef
  item: ImsItem
  adjustment_type: string
  direction: "in" | "out"
  quantity: number
  reason: string
  notes: string | null
  created_by: string | null
  created_at: string
  replayed?: boolean
}

export interface LedgerRow {
  id: string
  movement_type: string
  warehouse: WarehouseRef
  item: ImsItem
  quantity_delta: number
  quantity_before: number
  quantity_after: number
  reference_no: string | null
  transfer_id: string | null
  actor: string | null
  occurred_at: string
  notes: string | null
}

export interface NamedLocation {
  label: string
  id: string
  code: string
  name: string
}

export interface CategoryOverviewSku {
  sku: string
  packaging: string
  item_id: string | null
  status: string | null
  found_in_catalog: boolean
  by_warehouse: Record<string, number>
  total: number
}

/** A flavour (Herbal Masala) or a product (Ayurvedic/Nutraceutical) -- the
 * middle level of Category -> group -> SKU. */
export interface CategoryOverviewGroup {
  name: string
  skus: CategoryOverviewSku[]
  total: number
}

export interface CategoryOverviewEntry {
  category: string
  groups: CategoryOverviewGroup[]
  total_products: number
  total_skus: number
  total_stock: number
}

export interface UnclassifiedProduct {
  title: string
  sku: string
  current_category: string | null
  found_in_catalog: boolean
}

export interface CategoriesOverview {
  items: CategoryOverviewEntry[]
  locations: NamedLocation[]
  unclassified: UnclassifiedProduct[]
}

export interface Dashboard {
  filters: Record<string, string | null>
  cards: {
    total_physical_stock: number
    packaging_stock_by_unit: Record<string, number>
    total_warehouses: number
    active_warehouses: number
    low_stock_items: number
    out_of_stock_items: number
    recent_stock_movements: number
    tracked_finished_items: number
    tracked_packaging_items: number
  }
  warehouses: (ImsWarehouse & {
    distinct_products: number
    finished_skus: number
    finished_quantity: number
    packaging_quantity_by_unit: Record<string, number>
    low_stock_count: number
    movements_in_range: number
    last_activity: string | null
  })[]
  named_locations: (NamedLocation & { finished_quantity: number })[]
  stock_distribution: { warehouse: string; code: string; finished: number; packaging: number }[]
  category_stock: { category: string; quantity: number }[]
  low_stock: (ImsItem & { total: number; by_warehouse: Record<string, number> })[]
  out_of_stock: (ImsItem & { total: number; by_warehouse: Record<string, number> })[]
  recent_transfers: Transfer[]
  recent_returns: StockReturn[]
  recent_adjustments: Adjustment[]
  catalog: (CatalogStats & { truncated: boolean; fetched_at: number }) | null
  warnings: string[]
  categories: string[]
}

export interface ReportData {
  name: string
  title: string
  subtitle: string
  columns: string[]
  rows: (string | number)[][]
  truncated?: boolean
}

export const MOVEMENT_LABELS: Record<string, string> = {
  opening_balance: "Opening balance",
  inward: "Stock inward",
  outward: "Stock outward",
  transfer_out: "Transfer out",
  transfer_in: "Transfer in",
  return_in: "External return",
  adjustment_in: "Adjustment (+)",
  adjustment_out: "Adjustment (−)",
  damaged: "Damaged",
  missing: "Missing",
}

export const ADJUSTMENT_TYPES = [
  { value: "increase", label: "Increase in stock", direction: "in" },
  { value: "decrease", label: "Decrease in stock", direction: "out" },
  { value: "damaged", label: "Damaged stock", direction: "out" },
  { value: "missing", label: "Missing stock", direction: "out" },
  { value: "count_correction", label: "Correction after physical count", direction: null },
  { value: "other", label: "Other (justified)", direction: null },
] as const
