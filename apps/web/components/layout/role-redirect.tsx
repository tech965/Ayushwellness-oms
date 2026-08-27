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
 */
export function RoleRedirect() {
  const { user, hasRole } = useAuth()
  const pathname = usePathname()
  const router = useRouter()

  React.useEffect(() => {
    if (!user) return

    if (hasRole("TELECALLER") && !pathname.startsWith("/telecaller")) {
      router.replace("/telecaller/dashboard")
      return
    }
    if (hasRole("TEAM_LEADER") && !pathname.startsWith("/team")) {
      router.replace("/team/dashboard")
    }
  }, [user, hasRole, pathname, router])

  return null
}
