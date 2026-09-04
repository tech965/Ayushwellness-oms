"use client"

import * as React from "react"
import { CalendarClock, Landmark, PiggyBank, Wallet } from "lucide-react"
import { toast } from "sonner"

import { StatTile } from "@/components/shared/stat-tile"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDate, formatMoney } from "@/lib/format"
import { useCashfreeSettlementSummary, useSyncCashfreeSettlements } from "@/services/cashfree"

/** Cashfree SETTLEMENT state -- money Cashfree has paid out (or is about
 * to pay out) to the merchant bank account. Deliberately a separate
 * section/query from the Cashfree Transactions KPIs above it: a
 * settlement amount is never the same figure as transaction revenue
 * (PG service charge/tax/adjustments make the two differ) — see
 * `app/services/cashfree_sync_service.py`'s module docstring for which
 * figures below are Cashfree-native vs. derived from the synced
 * settlement list.
 */
export function CashfreeSettlementSection({
  dateFrom,
  dateTo,
}: {
  dateFrom: string
  dateTo: string
}) {
  const { hasPermission } = useAuth()
  const summaryQuery = useCashfreeSettlementSummary()
  const syncSettlements = useSyncCashfreeSettlements()
  const summary = summaryQuery.data

  function handleSync() {
    syncSettlements.mutate(
      { date_from: dateFrom, date_to: dateTo },
      {
        onSuccess: (result) => {
          toast.success(
            `Settlement sync completed\n\n${result.fetched} settlements fetched\n${result.applied} updated\n${result.duplicates} duplicates\n${result.failures} failures`,
            { duration: 8000 }
          )
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Cashfree Settlement</CardTitle>
        {hasPermission("payments.create") && (
          <Button
            variant="outline"
            size="sm"
            disabled={syncSettlements.isPending}
            onClick={handleSync}
          >
            {syncSettlements.isPending ? "Syncing..." : "Sync Settlements"}
          </Button>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {summaryQuery.isLoading && (
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full" />
            ))}
          </div>
        )}

        {/* A failed fetch must never look like "no settlements exist" --
         * shown instead of the tiles below, never alongside a fabricated
         * zero. */}
        {summaryQuery.isError && (
          <Alert variant="destructive">
            <AlertTitle>Unable to load Cashfree settlement data</AlertTitle>
            <AlertDescription className="flex flex-col gap-3">
              <span>{getApiErrorMessage(summaryQuery.error)}</span>
              <Button
                variant="outline"
                size="sm"
                className="w-fit"
                onClick={() => void summaryQuery.refetch()}
              >
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {summary && (
          <>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatTile
                label="Unsettled Amount"
                icon={Wallet}
                value={formatMoney(summary.unsettled_amount)}
                subtext="Derived from synced settlements"
                accent="amber"
              />
              <StatTile
                label="Upcoming Settlement"
                icon={CalendarClock}
                value={
                  summary.upcoming_settlement_amount
                    ? formatMoney(summary.upcoming_settlement_amount)
                    : "—"
                }
                subtext={summary.upcoming_settlement_status ?? "None pending"}
                accent="blue"
              />
              <StatTile
                label="Last Settled"
                icon={PiggyBank}
                value={
                  summary.last_settled_amount ? formatMoney(summary.last_settled_amount) : "—"
                }
                subtext={
                  summary.last_settled_date ? formatDate(summary.last_settled_date) : "No settlements yet"
                }
                accent="emerald"
              />
              <StatTile
                label="Settlement UTR"
                icon={Landmark}
                value={summary.last_settlement_utr ?? "—"}
                subtext={summary.last_settlement_status ?? undefined}
                accent="slate"
              />
            </div>

            {summary.history.length === 0 ? (
              <p className="text-muted-foreground text-sm">
                No settlements found. Click &ldquo;Sync Settlements&rdquo; to fetch them from
                Cashfree for the selected date range.
              </p>
            ) : (
              <div className="overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader className="bg-muted/40">
                    <TableRow className="hover:bg-transparent">
                      <TableHead>Date</TableHead>
                      <TableHead className="text-right">Transaction Amount</TableHead>
                      <TableHead className="text-right">Amount Settled</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>UTR</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary.history.map((row) => (
                      <TableRow key={row.cf_settlement_id}>
                        <TableCell>
                          {row.settlement_processed_on
                            ? formatDate(row.settlement_processed_on)
                            : "—"}
                        </TableCell>
                        <TableCell className="text-right">
                          {row.payment_amount ? formatMoney(row.payment_amount) : "—"}
                        </TableCell>
                        <TableCell className="text-right">
                          {row.amount_settled ? formatMoney(row.amount_settled) : "—"}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{row.status ?? "—"}</Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {row.settlement_utr ?? "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
