"use client"

import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { useSyncCashfreeTransactions } from "@/services/cashfree"

/** "Sync Cashfree Transactions" -- the bulk, operator-triggered
 * counterpart to the (already-real) Cashfree webhook: pulls every
 * transaction Cashfree reports for the currently selected date range
 * (`POST /payments/cashfree/sync`, reusing the exact same idempotent
 * `apply_payment_event` the webhook already uses) and reports exactly
 * what happened -- never a silent background refresh.
 */
export function CashfreeSyncTransactionsButton({
  dateFrom,
  dateTo,
}: {
  dateFrom: string
  dateTo: string
}) {
  const { hasPermission } = useAuth()
  const sync = useSyncCashfreeTransactions()

  if (!hasPermission("payments.create")) return null

  function handleSync() {
    sync.mutate(
      { date_from: dateFrom, date_to: dateTo },
      {
        onSuccess: (result) => {
          const lines = [
            "Cashfree sync completed",
            "",
            `${result.fetched} records fetched`,
            `${result.processed} processed`,
            `${result.duplicates} duplicates`,
            `${result.failures} failures`,
          ]
          if (result.failures > 0) {
            toast.error(lines.join("\n"), { duration: 8000 })
          } else {
            toast.success(lines.join("\n"), { duration: 8000 })
          }
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <Button variant="outline" size="sm" disabled={sync.isPending} onClick={handleSync}>
      {sync.isPending ? "Syncing..." : "Sync Cashfree Transactions"}
    </Button>
  )
}
