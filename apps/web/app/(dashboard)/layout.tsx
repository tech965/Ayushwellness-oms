import type { ReactNode } from "react"

import { AppShell } from "@/components/layout/app-shell"
import { RoleRedirect } from "@/components/layout/role-redirect"
import { AuthProvider } from "@/lib/auth-context"

export default function DashboardGroupLayout({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <RoleRedirect />
      <AppShell>{children}</AppShell>
    </AuthProvider>
  )
}
