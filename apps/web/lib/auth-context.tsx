"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import { getStoredAccessToken } from "@/lib/api-client"
import { getCurrentUser, logout as logoutRequest } from "@/services/auth"
import type { CurrentUser } from "@/types/auth"

interface AuthContextValue {
  user: CurrentUser | null
  permissions: Set<string>
  isLoading: boolean
  hasPermission: (code: string) => boolean
  /** Checks `user.roles` directly (unlike `hasPermission`, which resolves
   * against the permission-code set) — used only for UI-level role
   * branching (which nav/dashboard a Team Leader vs. Telecaller lands on).
   * Never the actual access-control boundary: that's enforced server-side
   * via permissions on every request, same as everywhere else in the app.
   */
  hasRole: (role: string) => boolean
  logout: () => void
}

const AuthContext = React.createContext<AuthContextValue | null>(null)

/**
 * Client-side route guard for the (dashboard) route group. Tokens live in
 * localStorage (see lib/api-client.ts), so there's nothing for Next.js
 * middleware to read — this is the auth boundary instead: no token (or an
 * invalid one) redirects to /login before any dashboard content renders.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const queryClient = useQueryClient()

  // Gated on a token actually being present so an already-known-logged-out
  // visitor doesn't fire (and log a noisy 401 for) a /auth/me call whose
  // outcome is a foregone conclusion — the effect below redirects either way.
  const query = useQuery({
    queryKey: ["auth", "me"],
    queryFn: getCurrentUser,
    enabled: typeof window !== "undefined" && Boolean(getStoredAccessToken()),
    retry: false,
    staleTime: 60_000,
  })

  React.useEffect(() => {
    if (!getStoredAccessToken() || query.isError) {
      router.replace("/login")
    }
  }, [query.isError, router])

  const logout = React.useCallback(() => {
    void logoutRequest().finally(() => {
      queryClient.clear()
      router.push("/login")
    })
  }, [queryClient, router])

  const permissions = React.useMemo(
    () => new Set(query.data?.permissions ?? []),
    [query.data?.permissions]
  )

  const hasPermission = React.useCallback(
    (code: string) => query.data?.is_superuser === true || permissions.has(code),
    [permissions, query.data?.is_superuser]
  )

  const hasRole = React.useCallback(
    (role: string) => query.data?.roles?.includes(role) ?? false,
    [query.data?.roles]
  )

  const value: AuthContextValue = {
    user: query.data ?? null,
    permissions,
    isLoading: query.isPending,
    hasPermission,
    hasRole,
    logout,
  }

  // No stored token at all (or it's already known invalid) — the effect
  // above is about to redirect to /login. Render nothing so dashboard
  // content never flashes on screen first. Only meaningful client-side:
  // during SSR/hydration this always renders children, since there's no
  // localStorage to check — an inherent tradeoff of client-only auth
  // state with no server-visible session cookie.
  if (typeof window !== "undefined" && !getStoredAccessToken()) return null

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = React.useContext(AuthContext)
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider")
  }
  return context
}
