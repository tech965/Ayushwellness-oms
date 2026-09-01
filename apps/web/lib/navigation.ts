import type { LucideIcon } from "lucide-react"
import {
  BarChart3,
  Bell,
  Boxes,
  CalendarClock,
  ClipboardList,
  CreditCard,
  FileClock,
  Gauge,
  History,
  LayoutDashboard,
  ListChecks,
  Package,
  PackageX,
  Phone,
  PhoneCall,
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
      { label: "Payments", href: "/payments", icon: CreditCard },
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

/** Minimal nav for TELECALLER — spec: "Do not show the full Admin OMS
 * navigation." The real access boundary is backend-side (`calls.manage`);
 * this is purely UI simplification for a role that has no business
 * reason to see the rest of the app's chrome.
 */
export const telecallerNavGroups: NavGroup[] = [
  {
    label: "Telecalling",
    items: [
      { label: "Dashboard", href: "/telecaller/dashboard", icon: LayoutDashboard },
      { label: "My Assigned Orders", href: "/telecaller/orders", icon: Phone },
      { label: "Follow-ups", href: "/telecaller/follow-ups", icon: CalendarClock },
      { label: "Call History", href: "/telecaller/calls", icon: PhoneCall },
    ],
  },
]

/** Minimal nav for TEAM_LEADER. */
export const teamLeaderNavGroups: NavGroup[] = [
  {
    label: "Team",
    items: [
      { label: "Dashboard", href: "/team/dashboard", icon: LayoutDashboard },
      { label: "Unfulfilled Orders", href: "/team/orders/unfulfilled", icon: ListChecks },
      { label: "Telecallers", href: "/team/telecallers", icon: Users },
    ],
  },
]
