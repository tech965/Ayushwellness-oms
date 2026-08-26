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
import { formatMoney } from "@/lib/format"
import type { TopProduct } from "@/types/analytics"

export function TopProductsCard({
  data,
  isLoading,
}: {
  data: TopProduct[] | undefined
  isLoading: boolean
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Top Selling Products</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="bg-muted h-40 w-full animate-pulse rounded-md" />
        ) : !data?.length ? (
          <p className="text-muted-foreground text-sm">No product sales in the selected range.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Product</TableHead>
                <TableHead className="text-right">Units Sold</TableHead>
                <TableHead className="text-right">Revenue</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((product) => (
                <TableRow key={product.sku} className="cursor-pointer">
                  <TableCell className="p-0">
                    <Link
                      href={`/orders?sku=${encodeURIComponent(product.sku)}`}
                      className="hover:text-primary flex flex-col px-4 py-2"
                    >
                      <span className="font-medium">{product.title}</span>
                      <span className="text-muted-foreground font-mono text-xs">{product.sku}</span>
                    </Link>
                  </TableCell>
                  <TableCell className="text-right">{product.units_sold}</TableCell>
                  <TableCell className="text-right">{formatMoney(product.revenue)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
