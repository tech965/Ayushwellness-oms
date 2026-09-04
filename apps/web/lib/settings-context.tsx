"use client"

import * as React from "react"

import { useSettings } from "@/services/settings"
import type { AppSettingsData } from "@/types/settings"

const SettingsContext = React.createContext<AppSettingsData | undefined>(undefined)

/** Fetches OMS settings once and makes them available to every hook/page
 * below it via `useAppSettings()`. Mounted once in the dashboard route
 * group's layout (`app/(dashboard)/layout.tsx`) -- deliberately a plain
 * context (not a hook every consumer calls directly), so a component
 * under test that renders outside this provider (every existing unit
 * test does) reads `undefined` and falls back to its own default rather
 * than firing an unmocked network request.
 */
export function SettingsProvider({ children }: { children: React.ReactNode }) {
  const { data } = useSettings()
  return (
    <SettingsContext.Provider value={data?.settings}>{children}</SettingsContext.Provider>
  )
}

/** `undefined` until the settings request resolves, or outside a
 * `SettingsProvider` entirely -- every caller must already have a sane
 * hardcoded fallback for that case (this never fetches on its own).
 */
export function useAppSettings(): AppSettingsData | undefined {
  return React.useContext(SettingsContext)
}
