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
 * user holds both (the broader, supervisory role).
 */
export function RoleRedirect() {
  const { user, hasRole } = useAuth()
  const pathname = usePathname()
  const router = useRouter()

  const targetSection = hasRole("TEAM_LEADER")
    ? "/team"
    : hasRole("TELECALLER")
      ? "/telecaller"
      : null

  React.useEffect(() => {
    if (!user || targetSection === null) return
    if (pathname.startsWith(targetSection)) return
    router.replace(`${targetSection}/dashboard`)
  }, [user, targetSection, pathname, router])

  return null
}
