import type { ReactNode } from "react"

import { ImsShell } from "@/components/ims/ims-shell"
import { AuthProvider } from "@/lib/auth-context"
import { SettingsProvider } from "@/lib/settings-context"

export default function ImsLayout({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <SettingsProvider>
        <ImsShell>{children}</ImsShell>
      </SettingsProvider>
    </AuthProvider>
  )
}
