import Link from "next/link"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { CourierPerformance } from "@/types/analytics"

function Pct({ value, tone }: { value: number; tone: "success" | "danger" }) {
  return (
    <span
      className={
        tone === "success"
          ? "text-emerald-600 dark:text-emerald-400"
          : "text-red-600 dark:text-red-400"
      }
    >
      {value.toFixed(1)}%
    </span>
  )
}

export function CourierPerformanceCard({
  data,
  isLoading,
}: {
  data: CourierPerformance[] | undefined
  isLoading: boolean
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Courier Performance</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="bg-muted h-40 w-full animate-pulse rounded-md" />
        ) : !data?.length ? (
          <p className="text-muted-foreground text-sm">No shipments in the selected range.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Courier</TableHead>
                <TableHead className="text-right">Shipments</TableHead>
                <TableHead className="text-right">Delivered</TableHead>
                <TableHead className="text-right">NDR</TableHead>
                <TableHead className="text-right">RTO</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((courier) => (
                <TableRow key={courier.courier_id}>
                  <TableCell className="p-0">
                    <Link
                      href={`/orders?courier_id=${courier.courier_id}`}
                      className="hover:text-primary block px-4 py-2 font-medium"
                    >
                      {courier.name}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right">{courier.shipment_count}</TableCell>
                  <TableCell className="text-right">
                    <Pct value={courier.delivered_pct} tone="success" />
                  </TableCell>
                  <TableCell className="text-right">
                    <Pct value={courier.ndr_pct} tone="danger" />
                  </TableCell>
                  <TableCell className="text-right">
                    <Pct value={courier.rto_pct} tone="danger" />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
