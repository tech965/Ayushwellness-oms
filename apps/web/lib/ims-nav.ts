import type { LucideIcon } from "lucide-react"
import {
  ArrowLeftRight,
  Boxes,
  ClipboardEdit,
  FileBarChart,
  History,
  LayoutDashboard,
  Package,
  Settings,
  Tags,
  Undo2,
  Warehouse,
  Barcode,
  PackageSearch,
} from "lucide-react"

export interface ImsNavItem {
  label: string
  href: string
  icon: LucideIcon
  permission?: string
}

export const imsNav: ImsNavItem[] = [
  { label: "Dashboard", href: "/ims", icon: LayoutDashboard },
  { label: "Products", href: "/ims/products", icon: Package },
  { label: "Categories", href: "/ims/categories", icon: Tags },
  { label: "SKUs", href: "/ims/skus", icon: Barcode },
  { label: "Warehouses", href: "/ims/warehouses", icon: Warehouse },
  { label: "Stock Overview", href: "/ims/stock", icon: PackageSearch },
  { label: "Stock Transfers", href: "/ims/transfers", icon: ArrowLeftRight, permission: "ims.read" },
  { label: "Stock Returns", href: "/ims/returns", icon: Undo2 },
  { label: "Packaging Inventory", href: "/ims/packaging", icon: Boxes },
  { label: "Stock Adjustments", href: "/ims/adjustments", icon: ClipboardEdit },
  { label: "Inventory History", href: "/ims/history", icon: History },
  { label: "Reports", href: "/ims/reports", icon: FileBarChart },
  { label: "Settings", href: "/ims/settings", icon: Settings, permission: "ims.manage" },
]

