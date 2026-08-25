"use client"

import * as React from "react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { formatDateTime } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useAuditLogs } from "@/services/audit-logs"
import type { AuditLog } from "@/types/audit-log"

export default function AuditLogsPage() {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [search, setSearch] = React.useState("")
  const [entityType, setEntityType] = React.useState("")

  const query = useAuditLogs({
    page,
    pageSize,
    action: search || undefined,
    entity_type: entityType || undefined,
  })

  const columns: DataTableColumn<AuditLog>[] = [
    {
      id: "action",
      header: "Action",
      cell: (log) => <span className="font-mono text-xs">{log.action}</span>,
    },
    {
      id: "entity",
      header: "Entity",
      cell: (log) => (
        <div className="flex items-center gap-1.5">
          <Badge variant="secondary">{log.entity_type}</Badge>
          <span className="text-muted-foreground font-mono text-xs">
            {log.entity_id.slice(0, 8)}
          </span>
        </div>
      ),
    },
    { id: "user", header: "User", cell: (log) => log.user_id?.slice(0, 8) ?? "system" },
    { id: "time", header: "Time", cell: (log) => formatDateTime(log.created_at) },
  ]

  return (
    <>
      <PageHeader
        title="Audit Logs"
        description="Every important OMS mutation, in order."
      />
      <div className="flex flex-col gap-4">
        <FilterBar
          searchValue={search}
          onSearchChange={(value) => {
            setSearch(value)
            resetPage()
          }}
          searchPlaceholder="Filter by action (e.g. order.created)..."
          extra={
            <Input
              value={entityType}
              onChange={(event) => {
                setEntityType(event.target.value)
                resetPage()
              }}
              placeholder="Entity type (e.g. order)..."
              className="w-[220px]"
            />
          }
        />
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No audit log entries"
          emptyDescription="Try adjusting your filters."
        >
          {(data) => (
            <>
              <DataTable columns={columns} data={data.data} rowKey={(log) => log.id} />
              <PaginationBar meta={data.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}
