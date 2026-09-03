import type { ReactNode } from "react"

import { AppShell } from "@/components/layout/app-shell"
import { RoleRedirect } from "@/components/layout/role-redirect"
import { SessionIdleGuard } from "@/components/layout/session-idle-guard"
import { AuthProvider } from "@/lib/auth-context"
import { SettingsProvider } from "@/lib/settings-context"

export default function DashboardGroupLayout({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <SettingsProvider>
        <RoleRedirect />
        <SessionIdleGuard />
        <AppShell>{children}</AppShell>
      </SettingsProvider>
    </AuthProvider>
  )
}
