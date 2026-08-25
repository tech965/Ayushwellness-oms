"use client"

import { useRouter } from "next/navigation"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { formatDateTime } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useIntegrations } from "@/services/integrations"
import type { Integration } from "@/types/integration"

export default function IntegrationsPage() {
  const router = useRouter()
  const { page, pageSize, setPage } = usePaginationState()
  const query = useIntegrations({ page, pageSize })

  const columns: DataTableColumn<Integration>[] = [
    { id: "name", header: "Integration", cell: (i) => i.name },
    { id: "type", header: "Type", cell: (i) => i.type },
    {
      id: "status",
      header: "Status",
      cell: (i) => <StatusBadge domain="integration" status={i.status} />,
    },
    {
      id: "last_sync",
      header: "Last Sync",
      cell: (i) => (i.last_sync_at ? formatDateTime(i.last_sync_at) : "Never"),
    },
    {
      id: "last_successful_sync",
      header: "Last Successful Sync",
      cell: (i) =>
        i.last_successful_sync_at ? formatDateTime(i.last_successful_sync_at) : "—",
    },
    {
      id: "last_failure",
      header: "Last Failure",
      cell: (i) => (i.last_failure_at ? formatDateTime(i.last_failure_at) : "—"),
    },
  ]

  return (
    <>
      <PageHeader
        title="Integrations"
        description="Shopify, Shiprocket, courier, and messaging connection status and sync history."
      />
      <div className="flex flex-col gap-4">
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No integrations configured"
          emptyDescription="Integration rows are seeded for every known provider (Shopify, Shiprocket, couriers, WhatsApp, Meta) — run the seed script if this list is empty."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(i) => i.id}
                onRowClick={(i) => router.push(`/integrations/${i.id}`)}
              />
              <PaginationBar meta={data.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}
