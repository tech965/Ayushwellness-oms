"use client"

import { useCallback, useSyncExternalStore } from "react"

type Listener = () => void

const listenersByKey = new Map<string, Set<Listener>>()
const snapshotCache = new Map<string, { raw: string | null; parsed: unknown }>()

function getListeners(key: string): Set<Listener> {
  let set = listenersByKey.get(key)
  if (!set) {
    set = new Set()
    listenersByKey.set(key, set)
  }
  return set
}

function readRaw(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

/**
 * A `localStorage`-backed piece of state, safe under SSR/hydration and
 * clean under `react-hooks/set-state-in-effect` — reading the persisted
 * value happens via `useSyncExternalStore`'s server/client snapshot split
 * (server and the first client paint both see `defaultValue`; the real
 * stored value appears on the very next tick, the same "flip after
 * hydration" trick `useMounted` uses) instead of a `useEffect` that calls
 * `setState`. `getSnapshot` is memoized per raw string so it returns a
 * stable reference across renders when nothing changed — required by
 * `useSyncExternalStore`, otherwise a fresh `deserialize()` object every
 * render looks like a constantly-changing store and either loops or warns.
 */
export function useLocalStorageState<T>(
  key: string,
  defaultValue: T,
  serialize: (value: T) => string = JSON.stringify,
  deserialize: (raw: string) => T = JSON.parse
): [T, (value: T) => void] {
  const getSnapshot = useCallback((): T => {
    const raw = readRaw(key)
    const cached = snapshotCache.get(key)
    if (cached && cached.raw === raw) return cached.parsed as T
    const parsed = raw !== null ? deserialize(raw) : defaultValue
    snapshotCache.set(key, { raw, parsed })
    return parsed
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const getServerSnapshot = useCallback(() => defaultValue, [defaultValue])

  const subscribe = useCallback(
    (listener: Listener) => {
      const set = getListeners(key)
      set.add(listener)
      return () => set.delete(listener)
    },
    [key]
  )

  const value = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)

  const setValue = useCallback(
    (next: T) => {
      try {
        window.localStorage.setItem(key, serialize(next))
      } catch {
        // Best-effort persistence only.
      }
      snapshotCache.delete(key)
      for (const listener of getListeners(key)) listener()
    },
    [key, serialize]
  )

  return [value, setValue]
}
