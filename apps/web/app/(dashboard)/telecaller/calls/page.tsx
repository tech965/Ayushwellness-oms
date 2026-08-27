"use client"

import Link from "next/link"

import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Card, CardContent } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { formatDateTime } from "@/lib/format"
import { useMyCallHistory } from "@/services/telecaller"

export default function TelecallerCallHistoryPage() {
  const query = useMyCallHistory()

  return (
    <>
      <PageHeader
        title="Call History"
        description="Every call you've logged, most recent first."
      />
      <Card>
        <CardContent>
          <QueryStates
            isLoading={query.isLoading}
            isError={query.isError}
            error={query.error}
            data={query.data}
            onRetry={() => void query.refetch()}
            isEmpty={(data) => data.length === 0}
            emptyTitle="No calls logged yet"
          >
            {(entries) => (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Order</TableHead>
                      <TableHead>Attempt</TableHead>
                      <TableHead>Date/Time</TableHead>
                      <TableHead>Outcome</TableHead>
                      <TableHead>Notes</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {entries.map((entry) => (
                      <TableRow key={entry.id}>
                        <TableCell>
                          <Link
                            href={`/telecaller/orders/${entry.order_id}`}
                            className="text-primary font-medium hover:underline"
                          >
                            {entry.order_number}
                          </Link>
                        </TableCell>
                        <TableCell>#{entry.attempt_number}</TableCell>
                        <TableCell>{formatDateTime(entry.attempted_at)}</TableCell>
                        <TableCell>
                          <StatusBadge domain="telecalling" status={entry.outcome} />
                        </TableCell>
                        <TableCell className="max-w-[300px] truncate">
                          {entry.notes ?? "—"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </QueryStates>
        </CardContent>
      </Card>
    </>
  )
}
