"use client"

import * as React from "react"

import { useAuth } from "@/lib/auth-context"
import { useAppSettings } from "@/lib/settings-context"

const ACTIVITY_EVENTS = ["mousedown", "keydown", "scroll", "touchstart"] as const

/** Enforces "Session timeout" (Administration -> Settings -> Security) by
 * logging the user out after that many minutes with no mouse/keyboard/
 * scroll/touch activity — 0 (the default) disables it entirely. Mounted
 * once per dashboard session in `app/(dashboard)/layout.tsx`, inside both
 * `AuthProvider` and `SettingsProvider`. Renders nothing; it's a timer,
 * not UI.
 */
export function SessionIdleGuard() {
  const { user, logout } = useAuth()
  const settings = useAppSettings()
  const timeoutMinutes = settings?.security.session_timeout_minutes ?? 0

  React.useEffect(() => {
    if (!user || timeoutMinutes <= 0) return undefined

    let timer: ReturnType<typeof setTimeout>
    const resetTimer = () => {
      clearTimeout(timer)
      timer = setTimeout(logout, timeoutMinutes * 60 * 1000)
    }

    resetTimer()
    for (const event of ACTIVITY_EVENTS) {
      window.addEventListener(event, resetTimer)
    }
    return () => {
      clearTimeout(timer)
      for (const event of ACTIVITY_EVENTS) {
        window.removeEventListener(event, resetTimer)
      }
    }
  }, [user, timeoutMinutes, logout])

  return null
}
