import type { LucideIcon } from "lucide-react"
import {
  Activity,
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
  Map,
  MousePointerClick,
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
  Warehouse,
  Workflow,
} from "lucide-react"

export interface NavItem {
  label: string
  href: string
  icon: LucideIcon
  /** Gates this item on `useAuth().hasPermission(code)` (see
   * `SidebarNav`) -- omit for items every `navGroups` user should see.
   */
  permission?: string
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
      { label: "Inventory", href: "/inventory", icon: Warehouse, permission: "inventory.read" },
    ],
  },
  {
    label: "Fulfillment",
    items: [
      { label: "Fulfillment Dashboard", href: "/fulfillment/dashboard", icon: Gauge },
      { label: "Confirmed Orders", href: "/fulfillment/orders", icon: ListChecks },
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
    items: [
      { label: "Analytics", href: "/analytics", icon: BarChart3 },
      { label: "🇮🇳 Supply Intelligence", href: "/supply-intelligence", icon: Map },
      {
        label: "🤖 Operations Command Center",
        href: "/operations-command-center",
        icon: Activity,
      },
    ],
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
    // Admin/superuser view of the same Telecalling pages `teamLeaderNavGroups`
    // links to below -- same routes, same icons, just surfaced in the full
    // Admin OMS menu too (previously only reachable by a Team Leader/
    // Telecaller account or a direct URL; Admin already has every
    // `telecalling.manage`/`calls.manage` permission via the "*" role).
    label: "Telecalling",
    items: [
      { label: "Dashboard", href: "/team/dashboard", icon: LayoutDashboard },
      { label: "Lead Pool", href: "/team/leads", icon: ListChecks },
      { label: "Abandoned Checkouts", href: "/team/checkouts", icon: MousePointerClick },
      { label: "Unfulfilled Orders", href: "/team/orders/unfulfilled", icon: ListChecks },
      { label: "Telecallers", href: "/team/telecallers", icon: Users },
    ],
  },
  {
    label: "Administration",
    items: [
      { label: "Users", href: "/users", icon: ShieldCheck, permission: "users.manage" },
      { label: "Roles", href: "/roles", icon: Gauge, permission: "roles.manage" },
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
      {
        label: "My Checkout Leads",
        href: "/telecaller/checkouts",
        icon: MousePointerClick,
      },
      { label: "Follow-ups", href: "/telecaller/follow-ups", icon: CalendarClock },
      { label: "Call History", href: "/telecaller/calls", icon: PhoneCall },
    ],
  },
]

/** Minimal nav for FULFILLMENT — a dedicated shipment-processing role.
 * Mirrors `telecallerNavGroups`: a focused module, not the full Admin OMS
 * menu. The real access boundary is backend-side (`shipments.read` /
 * `shipments.update` / `orders.read`); this is UI simplification only.
 * `/orders/[id]` and `/shipments/[id]` (reached from the queue's "Process
 * Shipment" action) stay usable — see `RoleRedirect`.
 */
export const fulfillmentNavGroups: NavGroup[] = [
  {
    label: "Fulfillment",
    items: [
      { label: "Fulfillment Dashboard", href: "/fulfillment/dashboard", icon: Gauge },
      { label: "Confirmed Orders", href: "/fulfillment/orders", icon: ListChecks },
      { label: "Shipments", href: "/fulfillment/shipments", icon: Truck },
      { label: "NDR", href: "/ndr", icon: PackageX, permission: "ndr.read" },
      { label: "RTO", href: "/rto", icon: RotateCcw, permission: "rto.read" },
    ],
  },
]

/** Minimal nav for SHIPMENT_STAFF — a dedicated, SCOPED shipment-
 * processing role (only orders confirmed by their own assigned
 * Telecallers, see `User.shipment_staff_id`). Deliberately its own nav
 * group, not a reuse of `fulfillmentNavGroups` — FULFILLMENT is
 * org-wide and must stay that way; conflating the two navs would risk
 * implying the same scope. No `permission` gate on NDR/RTO here (unlike
 * `fulfillmentNavGroups`, which gates on `ndr.read`/`rto.read` —
 * SHIPMENT_STAFF never holds those, only `shipment_staff.manage`, which
 * covers its own scoped `/shipment-staff/ndr`/`/shipment-staff/rto`
 * endpoints) — this whole nav group only ever renders for a user who
 * already holds the SHIPMENT_STAFF role.
 */
export const shipmentStaffNavGroups: NavGroup[] = [
  {
    label: "Shipment Staff",
    items: [
      { label: "My Shipment Dashboard", href: "/shipment-staff/dashboard", icon: Gauge },
      { label: "Confirmed Orders", href: "/shipment-staff/orders", icon: ListChecks },
      { label: "Shipments", href: "/shipment-staff/shipments", icon: Truck },
      { label: "NDR", href: "/shipment-staff/ndr", icon: PackageX },
      { label: "RTO", href: "/shipment-staff/rto", icon: RotateCcw },
    ],
  },
]

/** Minimal nav for TEAM_LEADER. */
export const teamLeaderNavGroups: NavGroup[] = [
  {
    label: "Team",
    items: [
      { label: "Dashboard", href: "/team/dashboard", icon: LayoutDashboard },
      { label: "Lead Pool", href: "/team/leads", icon: ListChecks },
      { label: "Abandoned Checkouts", href: "/team/checkouts", icon: MousePointerClick },
      { label: "Unfulfilled Orders", href: "/team/orders/unfulfilled", icon: ListChecks },
      { label: "Telecallers", href: "/team/telecallers", icon: Users },
    ],
  },
]
