"use client"

import type { ReactNode } from "react"

import { useLiveMode } from "@/services/live"

interface LiveModeGateProps {
  /** Shown while the deployment serves provider data live. */
  live: ReactNode
  /** The stored-data screen. Kept until the live-mode answer arrives (or if
   * it fails), so a hiccup never swaps screens. */
  children: ReactNode
}

/** Picks between a page's stored-data screen and its live replacement. */
export function LiveModeGate({ live, children }: LiveModeGateProps) {
  const liveMode = useLiveMode()
  return <>{liveMode.data?.live_mode ? live : children}</>
}
