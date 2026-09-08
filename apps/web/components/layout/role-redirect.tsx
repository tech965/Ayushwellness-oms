"use client"

import * as React from "react"
import { usePathname, useRouter } from "next/navigation"

import { useAuth } from "@/lib/auth-context"

/** UX polish, not the security boundary: a Telecaller/Team Leader who
 * lands on (or navigates directly to) an Admin-only route is bounced to
 * their own dashboard instead of a page whose data they can't fetch
 * anyway — every underlying API call for `/orders`, `/users`, etc.
 * already 403s for these two roles server-side (`require_permission`),
 * so this redirect only ever prevents a confusing "blank/error" page,
 * never the actual data leak.
 *
 * Production incident this fixes: roles aren't mutually exclusive under
 * this RBAC model (a user can hold more than one `Role` — see
 * `app.models.rbac`), and a user who held BOTH `TELECALLER` and
 * `TEAM_LEADER` hit an infinite navigation loop — on `/team/dashboard`,
 * the old TELECALLER check fired (not under `/telecaller`) and replaced
 * to `/telecaller/dashboard`; there, the old TEAM_LEADER check fired
 * (not under `/team`) and replaced right back, forever, confirmed live
 * in production logs. The two checks below used to be independent
 * `if`s, each blind to the other's role; they're now resolved to a
 * single `targetSection`, so at most one redirect direction can ever be
 * active for a given user — and once `pathname` is already under that
 * one section, the effect is a no-op, which is what makes it idempotent
 * regardless of how many roles a user holds. `TEAM_LEADER` wins when a
 * user holds both (the broader, supervisory role); `FULFILLMENT` is
 * lowest-priority, same single-direction guarantee.
 *
 * `FULFILLMENT` differs from the two telecalling roles in that its work
 * legitimately spans more than one route prefix: the confirmed-order
 * queue lives under `/fulfillment`, but the actual shipment processing
 * happens on the shared `/orders/[id]` and `/shipments/[id]` pages (both
 * already permission-checked server-side). Those prefixes are therefore
 * in-scope and never bounce a fulfillment user back to their dashboard.
 */
export function RoleRedirect() {
  const { user, hasRole } = useAuth()
  const pathname = usePathname()
  const router = useRouter()

  const redirectRule = React.useMemo(() => {
    if (hasRole("TEAM_LEADER")) return { home: "/team/dashboard", allowed: ["/team"] }
    if (hasRole("TELECALLER"))
      return { home: "/telecaller/dashboard", allowed: ["/telecaller"] }
    if (hasRole("FULFILLMENT"))
      return {
        home: "/fulfillment/dashboard",
        allowed: ["/fulfillment", "/orders", "/shipments", "/ndr", "/rto"],
      }
    return null
  }, [hasRole])

  React.useEffect(() => {
    if (!user || redirectRule === null) return
    const inScope = redirectRule.allowed.some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
    )
    if (inScope) return
    router.replace(redirectRule.home)
  }, [user, redirectRule, pathname, router])

  return null
}
