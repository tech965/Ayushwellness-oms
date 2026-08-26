"use client"

import { useSyncExternalStore } from "react"

const emptySubscribe = () => () => {}

/** True only once the component has hydrated on the client — the
 * React-recommended replacement (via `useSyncExternalStore`'s
 * server/client snapshot split) for the old `useState(false)` +
 * `useEffect(() => setMounted(true), [])` pattern, which
 * `react-hooks/set-state-in-effect` now flags: that pattern calls
 * `setState` synchronously inside an effect purely to distinguish the
 * server-rendered pass from the client one, which is exactly what
 * `useSyncExternalStore`'s dual snapshot exists for.
 */
export function useMounted(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false
  )
}
