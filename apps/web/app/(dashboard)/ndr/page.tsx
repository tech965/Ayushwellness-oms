"use client"

import * as React from "react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDate } from "@/lib/format"
import { useAuth } from "@/lib/auth-context"
import { usePaginationState } from "@/lib/use-pagination"
import { useNdrReattempt, useNdrs, useUpdateNdr } from "@/services/ndr"
import { NDR_STATUS_OPTIONS, type NDR, type NDRStatus } from "@/types/ndr"

function NdrStatusCell({ ndr }: { ndr: NDR }) {
  const { hasPermission } = useAuth()
  const update = useUpdateNdr(ndr.id)

  if (!hasPermission("ndr.update")) {
    return <StatusBadge domain="ndr" status={ndr.status} />
  }

  return (
    <Select
      value={ndr.status}
      onValueChange={(value) => {
        update.mutate(
          { status: value as NDRStatus },
          {
            onSuccess: () => toast.success("NDR updated."),
            onError: (error) => toast.error(getApiErrorMessage(error)),
          }
        )
      }}
    >
      <SelectTrigger size="sm" className="w-[190px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {NDR_STATUS_OPTIONS.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function NdrReattemptAction({ ndr }: { ndr: NDR }) {
  const { hasPermission } = useAuth()
  const [open, setOpen] = React.useState(false)
  const [address1, setAddress1] = React.useState("")
  const [address2, setAddress2] = React.useState("")
  const [phone, setPhone] = React.useState("")
  const reattempt = useNdrReattempt(ndr.id)

  if (!hasPermission("ndr.update")) return null

  function submit() {
    reattempt.mutate(
      { address_1: address1, address_2: address2 || undefined, phone },
      {
        onSuccess: () => {
          toast.success("Reattempt requested via Shiprocket.")
          setOpen(false)
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Reattempt
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Request delivery reattempt</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="address1">Address line 1</Label>
            <Input
              id="address1"
              value={address1}
              onChange={(e) => setAddress1(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="address2">Address line 2 (optional)</Label>
            <Input
              id="address2"
              value={address2}
              onChange={(e) => setAddress2(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="phone">Phone</Label>
            <Input id="phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button disabled={!address1 || !phone || reattempt.isPending} onClick={submit}>
            {reattempt.isPending ? "Requesting..." : "Request reattempt"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default function NdrPage() {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [status, setStatus] = React.useState<NDRStatus | undefined>(undefined)

  const query = useNdrs({ page, pageSize, status })

  const columns: DataTableColumn<NDR>[] = [
    {
      id: "reason",
      header: "Reason",
      cell: (ndr) => ndr.reason ?? ndr.external_reason ?? "—",
    },
    { id: "attempt", header: "Attempt", cell: (ndr) => ndr.attempt_number },
    { id: "status", header: "Status", cell: (ndr) => <NdrStatusCell ndr={ndr} /> },
    {
      id: "reattempt",
      header: "Reattempt date",
      cell: (ndr) => (ndr.reattempt_date ? formatDate(ndr.reattempt_date) : "—"),
    },
    { id: "created", header: "Created", cell: (ndr) => formatDate(ndr.created_at) },
    { id: "action", header: "", cell: (ndr) => <NdrReattemptAction ndr={ndr} /> },
  ]

  return (
    <>
      <PageHeader title="NDR" description="Non-delivery reports awaiting resolution." />
      <div className="flex flex-col gap-4">
        <FilterBar
          statusValue={status}
          onStatusChange={(value) => {
            setStatus(value as NDRStatus | undefined)
            resetPage()
          }}
          statusOptions={NDR_STATUS_OPTIONS}
          statusLabel="Status"
        />
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No NDR records"
          emptyDescription="NDR records arrive here once a Shiprocket NDR sync runs, or via Shiprocket webhooks in a future phase."
        >
          {(data) => (
            <>
              <DataTable columns={columns} data={data.data} rowKey={(ndr) => ndr.id} />
              <PaginationBar meta={data.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}
