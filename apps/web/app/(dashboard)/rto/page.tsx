"use client"

import * as React from "react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
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
import { useRtos, useUpdateRto } from "@/services/rto"
import { RTO_STATUS_OPTIONS, type RTO, type RTOStatus } from "@/types/rto"

function RtoStatusCell({ rto }: { rto: RTO }) {
  const { hasPermission } = useAuth()
  const update = useUpdateRto(rto.id)

  if (!hasPermission("rto.update")) {
    return <StatusBadge domain="rto" status={rto.status} />
  }

  return (
    <Select
      value={rto.status}
      onValueChange={(value) => {
        update.mutate(
          { status: value as RTOStatus },
          {
            onSuccess: () => toast.success("RTO updated."),
            onError: (error) => toast.error(getApiErrorMessage(error)),
          }
        )
      }}
    >
      <SelectTrigger size="sm" className="w-[170px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {RTO_STATUS_OPTIONS.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export default function RtoPage() {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [status, setStatus] = React.useState<RTOStatus | undefined>(undefined)

  const query = useRtos({ page, pageSize, status })

  const columns: DataTableColumn<RTO>[] = [
    {
      id: "reason",
      header: "Reason",
      cell: (rto) => rto.reason ?? rto.external_reason ?? "—",
    },
    { id: "status", header: "Status", cell: (rto) => <RtoStatusCell rto={rto} /> },
    {
      id: "initiated",
      header: "Initiated",
      cell: (rto) => (rto.initiated_at ? formatDate(rto.initiated_at) : "—"),
    },
    {
      id: "completed",
      header: "Completed",
      cell: (rto) => (rto.completed_at ? formatDate(rto.completed_at) : "—"),
    },
  ]

  return (
    <>
      <PageHeader title="RTO" description="Return-to-origin shipments." />
      <div className="flex flex-col gap-4">
        <FilterBar
          statusValue={status}
          onStatusChange={(value) => {
            setStatus(value as RTOStatus | undefined)
            resetPage()
          }}
          statusOptions={RTO_STATUS_OPTIONS}
          statusLabel="Status"
        />
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No RTO records"
          emptyDescription="RTO records are derived automatically from Shiprocket tracking refreshes."
        >
          {(data) => (
            <>
              <DataTable columns={columns} data={data.data} rowKey={(rto) => rto.id} />
              <PaginationBar meta={data.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}
