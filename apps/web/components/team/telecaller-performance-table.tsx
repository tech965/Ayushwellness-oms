"use client"

import { useRouter } from "next/navigation"

import { QueryStates } from "@/components/shared/query-states"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useTeamTelecallers } from "@/services/team"

/** Shared by the Team Dashboard and the Telecallers list page — clicking
 * a row drills into that telecaller's assigned workload.
 */
export function TelecallerPerformanceTable() {
  const router = useRouter()
  const query = useTeamTelecallers()

  return (
    <Card>
      <CardHeader>
        <CardTitle>Telecaller Performance</CardTitle>
      </CardHeader>
      <CardContent>
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.length === 0}
          emptyTitle="No telecallers yet"
          emptyDescription="Add telecallers under your team from Users administration."
        >
          {(rows) => (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Telecaller</TableHead>
                    <TableHead className="text-right">Assigned</TableHead>
                    <TableHead className="text-right">Called</TableHead>
                    <TableHead className="text-right">Connected</TableHead>
                    <TableHead className="text-right">Follow-ups</TableHead>
                    <TableHead className="text-right">Confirmed</TableHead>
                    <TableHead className="text-right">Not Interested</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow
                      key={row.telecaller_id}
                      className="hover:bg-accent/60 cursor-pointer"
                      onClick={() =>
                        router.push(`/team/telecallers/${row.telecaller_id}`)
                      }
                    >
                      <TableCell className="font-medium">{row.telecaller_name}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.assigned}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.called}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.connected}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.follow_ups}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.confirmed}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {row.not_interested}
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
  )
}
