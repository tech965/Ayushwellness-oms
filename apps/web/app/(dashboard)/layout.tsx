import type { ReactNode } from "react"

import { AppShell } from "@/components/layout/app-shell"
import { AuthProvider } from "@/lib/auth-context"

export default function DashboardGroupLayout({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <AppShell>{children}</AppShell>
    </AuthProvider>
  )
}
