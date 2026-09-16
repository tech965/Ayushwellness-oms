"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { toast } from "sonner"

import {
  mergeOrderSequencePage,
  readOrderSequence,
  saveOrderSequence,
} from "@/lib/telecaller-order-sequence"
import { fetchMyOrders } from "@/services/telecaller"

/** Requirement 2 (review meeting): J = next order, K = previous order,
 * following whatever sequence the telecaller's own order list
 * (`/telecaller/orders`) most recently showed them — see
 * `lib/telecaller-order-sequence.ts` for why that sequence has to be
 * recorded by the list page and read back here rather than re-derived.
 *
 * Within the already-loaded page this is a pure client-side lookup (no
 * request at all). Only at a page boundary does this fetch exactly one
 * adjacent page — through the same `GET /telecaller/orders` the list
 * page itself uses, so it's automatically scoped to the telecaller's own
 * assignments server-side and can never reach another telecaller's
 * order — never more than that one request per boundary crossing.
 */
export function useOrderNavigation(orderId: string) {
  const router = useRouter()
  const [isPending, setIsPending] = React.useState(false)

  const go = React.useCallback(
    async (direction: 1 | -1) => {
      if (isPending) return
      const sequence = readOrderSequence()
      if (!sequence || sequence.ids.length === 0) {
        toast.info("Open an order from “My Assigned Orders” to enable J/K navigation.")
        return
      }
      const index = sequence.ids.indexOf(orderId)
      if (index === -1) {
        toast.info("Open an order from “My Assigned Orders” to enable J/K navigation.")
        return
      }

      const targetIndex = index + direction
      if (targetIndex >= 0 && targetIndex < sequence.ids.length) {
        router.push(`/telecaller/orders/${sequence.ids[targetIndex]}`)
        return
      }

      const targetPage = direction === 1 ? sequence.page + 1 : sequence.page - 1
      if (targetPage < 1 || targetPage > sequence.totalPages) {
        toast.info(direction === 1 ? "This is the last order." : "This is the first order.")
        return
      }

      setIsPending(true)
      try {
        const result = await fetchMyOrders({
          page: targetPage,
          pageSize: sequence.filters.pageSize,
          call_status: sequence.filters.call_status,
          date_from: sequence.filters.date_from,
          date_to: sequence.filters.date_to,
          q: sequence.filters.q,
        })
        const ids = result.data.map((row) => row.order_id)
        saveOrderSequence(
          mergeOrderSequencePage(sequence, {
            ids,
            page: targetPage,
            totalPages: result.meta.total_pages,
            filters: sequence.filters,
          })
        )
        const target = direction === 1 ? ids[0] : ids[ids.length - 1]
        if (target) {
          router.push(`/telecaller/orders/${target}`)
        } else {
          toast.info(direction === 1 ? "This is the last order." : "This is the first order.")
        }
      } catch {
        toast.error("Couldn't load the next order. Try again from the order list.")
      } finally {
        setIsPending(false)
      }
    },
    [isPending, orderId, router]
  )

  return {
    goToNext: React.useCallback(() => void go(1), [go]),
    goToPrevious: React.useCallback(() => void go(-1), [go]),
  }
}

/** Elements/states J/K must never fire inside — a text field the
 * telecaller is typing "j"/"k" into, or while any Radix Dialog/AlertDialog/
 * Select/DropdownMenu is capturing keyboard focus (Escape/Tab are already
 * claimed there; this component's own dialog-open flags cover that half).
 */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    target.isContentEditable ||
    target.closest("[role='dialog']") !== null
  )
}

/** Wires J/K to `goToNext`/`goToPrevious` on `window`, skipping every
 * case listed on `isTypingTarget` plus any modifier-key combination
 * (never hijack Cmd/Ctrl+J browser shortcuts) and `disabled` (any Radix
 * Dialog/AlertDialog open, tracked by the caller's own state — belt and
 * suspenders alongside the `[role='dialog']` check above, since a
 * dialog's own content can render arbitrary non-focused elements a click
 * might still land JS focus outside of).
 */
export function useOrderNavigationHotkeys(
  goToNext: () => void,
  goToPrevious: () => void,
  disabled: boolean
) {
  React.useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (disabled) return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (isTypingTarget(event.target)) return
      if (event.key === "j" || event.key === "J") {
        event.preventDefault()
        goToNext()
      } else if (event.key === "k" || event.key === "K") {
        event.preventDefault()
        goToPrevious()
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [goToNext, goToPrevious, disabled])
}
