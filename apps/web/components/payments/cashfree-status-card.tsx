"use client"

import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime } from "@/lib/format"
import { useAuth } from "@/lib/auth-context"
import { STATUS_TONE_CLASSES } from "@/lib/status-styles"
import { cn } from "@/lib/utils"
import { useCashfreeStatus, useTestCashfreeConnection } from "@/services/cashfree"
import type { CashfreeConnectionTest, CashfreeStatus } from "@/types/cashfree"

function connectionToneClass(
  status: CashfreeStatus | undefined,
  lastResult: CashfreeConnectionTest | undefined
): string {
  if (!status?.configured) return STATUS_TONE_CLASSES.neutral
  if (lastResult === undefined) return STATUS_TONE_CLASSES.neutral
  return lastResult.connected ? STATUS_TONE_CLASSES.success : STATUS_TONE_CLASSES.danger
}

function environmentLabel(environment: string): string {
  if (environment === "production") return "Production"
  if (environment === "sandbox") return "Sandbox"
  return "Not Configured"
}

/** Cashfree connection/status card for the `/payments` dashboard.
 * `useCashfreeStatus` is a pure config snapshot (no network call, safe on
 * every page load); "Test Connection" is the only thing that ever makes a
 * live, read-only Cashfree API call, and only when clicked. Never
 * displays a client secret, webhook secret, or token — the backend
 * (`CashfreeStatusResponse`/`CashfreeConnectionTestResponse`) never
 * returns one.
 */
export function CashfreeStatusCard() {
  const { hasPermission } = useAuth()
  const statusQuery = useCashfreeStatus()
  const testConnection = useTestCashfreeConnection()
  const status = statusQuery.data

  function handleTestConnection() {
    testConnection.mutate(undefined, {
      onSuccess: (result) => {
        if (!result) return
        if (result.connected) {
          toast.success("Cashfree is reachable and credentials are valid.")
        } else if (result.error_type === "not_configured") {
          toast.error("Cashfree is not configured.")
        } else {
          toast.error(`Cashfree connection test failed (${result.error_type ?? "unknown"}).`)
        }
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  const lastResult = testConnection.data ?? undefined

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2">
          Cashfree Connection
          <Badge variant="outline" className="border-transparent bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400">
            Payment Gateway
          </Badge>
        </CardTitle>
        {status && (
          <Badge
            variant="outline"
            className={cn(
              "rounded-md border-transparent font-semibold",
              connectionToneClass(status, lastResult)
            )}
          >
            {!status.configured
              ? "Not Configured"
              : lastResult === undefined
                ? "Not Tested"
                : lastResult.connected
                  ? "Connected"
                  : "Connection Failed"}
          </Badge>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {statusQuery.isLoading && (
          <p className="text-muted-foreground text-sm">Loading...</p>
        )}
        {statusQuery.isError && (
          <p className="text-destructive text-sm">
            {getApiErrorMessage(statusQuery.error)}
          </p>
        )}

        {status && (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Environment" value={environmentLabel(status.environment)} />
            <Stat label="API URL" value={status.api_url ?? "—"} mono />
            <Stat label="API Version" value={status.api_version ?? "—"} />
            <Stat
              label="Last connection result"
              value={
                lastResult
                  ? `${lastResult.connected ? "Reachable" : "Unreachable"} · ${formatDateTime(lastResult.checked_at)}`
                  : "Not tested yet"
              }
            />
          </div>
        )}

        {hasPermission("integrations.test") && (
          <div>
            <Button
              variant="outline"
              size="sm"
              disabled={testConnection.isPending || !status?.configured}
              onClick={handleTestConnection}
            >
              {testConnection.isPending ? "Testing..." : "Test Connection"}
            </Button>
            {lastResult && !lastResult.connected && lastResult.error_type && (
              <p className="text-danger mt-2 text-xs">
                {lastResult.error_type === "authentication_error"
                  ? "Cashfree rejected the configured credentials."
                  : `Error: ${lastResult.error_type}`}
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className={mono ? "font-mono text-sm break-all" : "text-sm font-medium break-words"}>
        {value}
      </p>
    </div>
  )
}
