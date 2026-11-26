"use client"

import * as React from "react"
import { CloudDownload } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import { useCashfreeSyncNow, useCashfreeSyncStatus } from "@/services/remittance"
import type { CashfreeConnectionStatus } from "@/types/remittance"

const CONNECTION: Record<CashfreeConnectionStatus, { label: string; className: string }> =
  {
    connected: {
      label: "Connected",
      className:
        "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
    },
    never_synced: {
      label: "Not synced yet",
      className: "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200",
    },
    degraded: {
      label: "Partially synced",
      className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
    },
    error: {
      label: "Sync failed",
      className: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
    },
    not_configured: {
      label: "Not Configured",
      className: "bg-slate-200 text-slate-800 dark:bg-slate-700 dark:text-slate-100",
    },
  }

const POLL_LIMIT_MS = 180_000

interface Watch {
  /** `last_attempt_at` when the sync was started: a newer attempt means it ended. */
  since: string | null
  expired: boolean
}

/** Cashfree is the prepaid gateway, so settlements are fetched from its API
 * automatically (scheduled) and on demand ("Sync Now"). This panel shows the
 * real connection state and the last SUCCESSFUL sync, never invented data:
 * with no credentials or API permission it says "Not Configured" and the file
 * upload below stays available as a fallback. A Cashfree "settled" status is
 * not proof of a bank credit; bank reconciliation is a separate step.
 */
export function CashfreeSyncPanel({ canSync }: { canSync: boolean }) {
  const [watch, setWatch] = React.useState<Watch | null>(null)
  const notified = React.useRef<Watch | null>(null)
  const syncNow = useCashfreeSyncNow()
  const queryClient = useQueryClient()
  const status = useCashfreeSyncStatus(watch && !watch.expired ? watch.since : undefined)
  const data = status.data

  const finished = Boolean(
    watch && data && !data.running && !data.queued && data.last_attempt_at !== watch.since
  )
  const watching = watch !== null && !watch.expired && !finished

  React.useEffect(() => {
    if (!watch || watch.expired) return
    const timer = setTimeout(() => setWatch({ ...watch, expired: true }), POLL_LIMIT_MS)
    return () => clearTimeout(timer)
  }, [watch])

  React.useEffect(() => {
    if (!finished || !watch || !data || notified.current === watch) return
    notified.current = watch
    // The sync ran in the background: refresh every reconciliation figure.
    void queryClient.invalidateQueries({ queryKey: ["remittance"] })
    if (data.last_sync_status === "ok") toast.success("Cashfree sync completed.")
    else if (data.connection_status === "not_configured")
      toast.error("Cashfree is not configured.")
    else toast.error(data.last_sync_error ?? "Cashfree sync did not complete.")
  }, [finished, watch, data, queryClient])

  async function runSync() {
    try {
      const result = await syncNow.mutateAsync()
      if (["queued", "already_queued", "already_running"].includes(result.status)) {
        setWatch({
          since: data?.last_attempt_at ?? null,
          expired: false,
        })
        toast.info(result.message ?? "Cashfree sync queued.")
      } else if (result.status === "not_configured") {
        toast.error(result.message ?? "Cashfree is not configured.")
      } else {
        toast.success("Cashfree sync finished.")
      }
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  if (!data) {
    return (
      <div className="rounded-lg border p-4 text-sm" data-testid="cashfree-sync-panel">
        <span className="text-muted-foreground">
          {status.isError
            ? "Cashfree sync status is unavailable."
            : "Loading Cashfree sync status…"}
        </span>
      </div>
    )
  }

  const conn = CONNECTION[data.connection_status]
  const running = data.running || Boolean(data.queued) || watching
  const byStatus = Object.entries(data.settlement_lots_by_status)
    .map(([name, count]) => `${count} ${name.replaceAll("_", " ").toLowerCase()}`)
    .join(" · ")

  return (
    <div className="space-y-2 rounded-lg border p-4" data-testid="cashfree-sync-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <CloudDownload className="text-muted-foreground size-4" />
          <h3 className="text-sm font-semibold">Cashfree settlements (API)</h3>
          <Badge
            variant="outline"
            className={cn("rounded-md border-transparent font-semibold", conn.className)}
            data-testid="cashfree-connection"
          >
            {conn.label}
          </Badge>
          {data.environment === "sandbox" && data.configured && (
            <Badge variant="outline" className="rounded-md">
              Sandbox
            </Badge>
          )}
        </div>
        {canSync && (
          <Button
            size="sm"
            onClick={() => void runSync()}
            disabled={!data.configured || running || syncNow.isPending}
          >
            <CloudDownload className="size-4" />
            {data.queued ? "Queued…" : running ? "Syncing…" : "Sync Now"}
          </Button>
        )}
      </div>

      {data.connection_status === "not_configured" ? (
        <p className="text-muted-foreground text-sm" role="status">
          {data.connection_message} No Cashfree data is shown. Use the fallback file
          upload below.
        </p>
      ) : (
        <div className="text-muted-foreground space-y-1 text-sm">
          <p data-testid="cashfree-last-sync">
            Last successful sync:{" "}
            <span className="text-foreground font-medium">
              {data.last_successful_sync_at
                ? formatDateTime(data.last_successful_sync_at)
                : "never"}
            </span>
            {data.sync_enabled && ` · automatic every ${data.sync_interval_minutes} min`}
          </p>
          {data.last_sync_error && data.connection_status !== "connected" && (
            <p className="text-amber-700 dark:text-amber-400" role="alert">
              Last attempt{" "}
              {data.last_attempt_at ? formatDateTime(data.last_attempt_at) : ""}:{" "}
              {data.last_sync_error}
            </p>
          )}
          <p>
            {data.settlement_lots} settlement lot{data.settlement_lots === 1 ? "" : "s"}
            {byStatus ? ` (${byStatus})` : ""} · {data.synced_lines} synced transaction
            line
            {data.synced_lines === 1 ? "" : "s"}
            {data.last_webhook_at &&
              ` · last settlement webhook ${formatDateTime(data.last_webhook_at)}`}
          </p>
        </div>
      )}
      {data.queued && (
        <p className="text-muted-foreground text-sm" role="status">
          Sync queued. Waiting for the background worker to pick it up.
        </p>
      )}
      <p className="text-muted-foreground text-xs">{data.bank_confirmation_note}</p>
    </div>
  )
}
