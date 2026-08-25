import type { LucideIcon } from "lucide-react"
import {
  BarChart3,
  Bell,
  Boxes,
  ClipboardList,
  FileClock,
  Gauge,
  History,
  LayoutDashboard,
  Package,
  PackageX,
  Plug,
  Repeat,
  RotateCcw,
  Scale,
  Settings,
  ShieldCheck,
  ShoppingCart,
  Truck,
  Users,
  Workflow,
} from "lucide-react"

export interface NavItem {
  label: string
  href: string
  icon: LucideIcon
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

export const navGroups: NavGroup[] = [
  {
    label: "Overview",
    items: [{ label: "Dashboard", href: "/dashboard", icon: LayoutDashboard }],
  },
  {
    label: "Commerce",
    items: [
      { label: "Orders", href: "/orders", icon: ShoppingCart },
      { label: "Customers", href: "/customers", icon: Users },
      { label: "Products", href: "/products", icon: Package },
    ],
  },
  {
    label: "Fulfillment",
    items: [
      { label: "Shipments", href: "/shipments", icon: Truck },
      { label: "NDR", href: "/ndr", icon: PackageX },
      { label: "RTO", href: "/rto", icon: RotateCcw },
      { label: "Returns", href: "/returns", icon: Repeat },
      { label: "Refunds", href: "/refunds", icon: FileClock },
      { label: "Couriers", href: "/couriers", icon: Boxes },
    ],
  },
  {
    label: "Intelligence",
    items: [{ label: "Analytics", href: "/analytics", icon: BarChart3 }],
  },
  {
    label: "Operations",
    items: [
      { label: "Integrations", href: "/integrations", icon: Plug },
      { label: "Reconciliation", href: "/reconciliation", icon: Scale },
      { label: "Automation", href: "/automation", icon: Workflow },
      { label: "Alerts", href: "/alerts", icon: Bell },
      { label: "Tasks", href: "/tasks", icon: ClipboardList },
      { label: "Audit Logs", href: "/audit-logs", icon: History },
    ],
  },
  {
    label: "Administration",
    items: [
      { label: "Users", href: "/users", icon: ShieldCheck },
      { label: "Roles", href: "/roles", icon: Gauge },
      { label: "Settings", href: "/settings", icon: Settings },
    ],
  },
]

export const allNavItems: NavItem[] = navGroups.flatMap((group) => group.items)
