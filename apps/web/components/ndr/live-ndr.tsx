"use client"

import * as React from "react"
import Link from "next/link"
import { toast } from "sonner"

import { PageHeader } from "@/components/shared/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { useLiveNdr, useLiveNdrReattempt, type LiveNdrItem } from "@/services/live"

function ReattemptForm({ item, onDone }: { item: LiveNdrItem; onDone: () => void }) {
  const [address, setAddress] = React.useState("")
  const [phone, setPhone] = React.useState("")
  const mutation = useLiveNdrReattempt()
  if (!item.shipment_id) return null
  const shipmentId = item.shipment_id

  return (
    <form
      className="flex flex-wrap items-end gap-2 py-2"
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate(
          { shipmentId, address_1: address, phone },
          {
            onSuccess: () => {
              toast.success("Reattempt requested.")
              onDone()
            },
            onError: (error) => toast.error(getApiErrorMessage(error)),
          }
        )
      }}
    >
      <Input
        required
        placeholder="Delivery address"
        value={address}
        onChange={(e) => setAddress(e.target.value)}
        aria-label="Delivery address"
      />
      <Input
        required
        placeholder="Phone"
        value={phone}
        onChange={(e) => setPhone(e.target.value)}
        aria-label="Phone"
      />
      <Button type="submit" size="sm" disabled={mutation.isPending}>
        Request reattempt
      </Button>
    </form>
  )
}

/** NDR list read live from Shiprocket. A row links to the OMS order only
 * when this OMS knows the shipment; others are shown as Shiprocket-only. */
export function LiveNdr() {
  const [page, setPage] = React.useState(1)
  const [open, setOpen] = React.useState<string | null>(null)
  const query = useLiveNdr(page)

  return (
    <>
      <PageHeader
        title="NDR"
        description="Read live from Shiprocket. Nothing on this page is stored."
      />
      {query.isError ? (
        <Alert variant="destructive">
          <AlertTitle>Could not load NDRs from Shiprocket</AlertTitle>
          <AlertDescription>{getApiErrorMessage(query.error)}</AlertDescription>
        </Alert>
      ) : null}
      {query.isLoading ? <Skeleton className="h-40 w-full" /> : null}
      {query.data ? (
        query.data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No open NDRs on this page.</p>
        ) : (
          <table className="w-full text-sm" data-testid="live-ndr">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-2">AWB</th>
                <th>Courier</th>
                <th>Reason</th>
                <th>Attempt</th>
                <th>Order</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((item) => (
                <React.Fragment key={`${item.awb}-${item.attempt_number}`}>
                  <tr className="border-t">
                    <td className="py-2">{item.awb ?? "—"}</td>
                    <td>{item.courier_name ?? "—"}</td>
                    <td>{item.reason ?? "—"}</td>
                    <td>{item.attempt_number}</td>
                    <td>
                      {item.order_id ? (
                        <Link className="underline" href={`/orders/${item.order_id}`}>
                          Open order
                        </Link>
                      ) : (
                        <span className="text-muted-foreground">Shiprocket only</span>
                      )}
                      {item.shipment_id ? (
                        <Button
                          variant="link"
                          size="sm"
                          onClick={() => setOpen(open === item.awb ? null : item.awb)}
                        >
                          Reattempt
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                  {open === item.awb && item.awb ? (
                    <tr>
                      <td colSpan={5}>
                        <ReattemptForm item={item} onDone={() => setOpen(null)} />
                      </td>
                    </tr>
                  ) : null}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        )
      ) : null}
      <div className="flex items-center gap-2 pt-2">
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
          Previous
        </Button>
        <span className="text-sm text-muted-foreground">Page {page}</span>
        <Button
          variant="outline"
          size="sm"
          disabled={!query.data?.has_more}
          onClick={() => setPage(page + 1)}
        >
          Next
        </Button>
      </div>
    </>
  )
}
