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
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import {
  useReconciliationResults,
  useReconciliationRuns,
  useResolveReconciliationResult,
  useTriggerReconciliationRun,
} from "@/services/reconciliation"
import {
  RECONCILIATION_RESULT_STATUS_OPTIONS,
  type ReconciliationResult,
  type ReconciliationResultStatus,
  type ReconciliationRun,
} from "@/types/reconciliation"

function TriggerRunButton() {
  const { hasPermission } = useAuth()
  const trigger = useTriggerReconciliationRun()

  if (!hasPermission("reconciliation.manage")) return null

  return (
    <Button
      size="sm"
      disabled={trigger.isPending}
      onClick={() =>
        trigger.mutate(undefined, {
          onSuccess: (run) =>
            toast.success(
              run?.status === "running"
                ? "Reconciliation run queued."
                : "Reconciliation run created."
            ),
          onError: (error) => toast.error(getApiErrorMessage(error)),
        })
      }
    >
      {trigger.isPending ? "Starting…" : "Run Reconciliation"}
    </Button>
  )
}

function ResolveAction({ result }: { result: ReconciliationResult }) {
  const { hasPermission } = useAuth()
  const resolve = useResolveReconciliationResult()

  if (result.resolved) {
    return <span className="text-muted-foreground text-xs">Resolved</span>
  }
  if (!hasPermission("reconciliation.manage")) {
    return null
  }

  return (
    <Button
      size="sm"
      variant="outline"
      disabled={resolve.isPending}
      onClick={() =>
        resolve.mutate(result.id, {
          onSuccess: () => toast.success("Marked resolved."),
          onError: (error) => toast.error(getApiErrorMessage(error)),
        })
      }
    >
      Resolve
    </Button>
  )
}

function RunsSection({
  selectedRunId,
  onSelectRun,
}: {
  selectedRunId: string | null
  onSelectRun: (id: string | null) => void
}) {
  const { page, pageSize, setPage } = usePaginationState()
  const query = useReconciliationRuns({ page, pageSize })

  const columns: DataTableColumn<ReconciliationRun>[] = [
    {
      id: "status",
      header: "Status",
      cell: (run) => <StatusBadge domain="reconciliation_run" status={run.status} />,
    },
    {
      id: "started",
      header: "Started",
      cell: (run) => (run.started_at ? formatDateTime(run.started_at) : "—"),
    },
    {
      id: "completed",
      header: "Completed",
      cell: (run) => (run.completed_at ? formatDateTime(run.completed_at) : "—"),
    },
    { id: "checked", header: "Checked", cell: (run) => run.total_checked },
    {
      id: "mismatches",
      header: "Mismatch / Missing / Error",
      cell: (run) => `${run.mismatch_count} / ${run.missing_count} / ${run.error_count}`,
    },
  ]

  return (
    <QueryStates
      isLoading={query.isLoading}
      isError={query.isError}
      error={query.error}
      data={query.data}
      onRetry={() => void query.refetch()}
      isEmpty={(data) => data.data.length === 0}
      emptyTitle="No reconciliation runs yet"
      emptyDescription="Trigger a run to compare OMS records against Shopify and Shiprocket."
    >
      {(data) => (
        <>
          <DataTable
            columns={columns}
            data={data.data}
            rowKey={(run) => run.id}
            onRowClick={(run) => onSelectRun(run.id === selectedRunId ? null : run.id)}
          />
          <PaginationBar meta={data.meta} onPageChange={setPage} />
        </>
      )}
    </QueryStates>
  )
}

function ResultsSection({ runId }: { runId: string | null }) {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [status, setStatus] = React.useState<ReconciliationResultStatus | undefined>(
    undefined
  )
  const query = useReconciliationResults({
    page,
    pageSize,
    run_id: runId ?? undefined,
    status,
  })

  const columns: DataTableColumn<ReconciliationResult>[] = [
    {
      id: "status",
      header: "Status",
      cell: (result) => (
        <StatusBadge domain="reconciliation_result" status={result.status} />
      ),
    },
    { id: "check", header: "Check", cell: (result) => result.check_type },
    { id: "provider", header: "Provider", cell: (result) => result.provider },
    { id: "entity", header: "Entity", cell: (result) => result.entity_type },
    { id: "message", header: "Details", cell: (result) => result.message ?? "—" },
    {
      id: "created",
      header: "Detected",
      cell: (result) => formatDateTime(result.created_at),
    },
    { id: "action", header: "", cell: (result) => <ResolveAction result={result} /> },
  ]

  return (
    <div className="flex flex-col gap-4">
      <FilterBar
        statusValue={status}
        onStatusChange={(value) => {
          setStatus(value as ReconciliationResultStatus | undefined)
          resetPage()
        }}
        statusOptions={RECONCILIATION_RESULT_STATUS_OPTIONS}
        statusLabel="Status"
      />
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
        isEmpty={(data) => data.data.length === 0}
        emptyTitle="Nothing to reconcile"
        emptyDescription={
          runId
            ? "This run found no results matching the current filter."
            : "Every checked record matched, or no run has been triggered yet."
        }
      >
        {(data) => (
          <>
            <DataTable
              columns={columns}
              data={data.data}
              rowKey={(result) => result.id}
            />
            <PaginationBar meta={data.meta} onPageChange={setPage} />
          </>
        )}
      </QueryStates>
    </div>
  )
}

export default function ReconciliationPage() {
  const [selectedRunId, setSelectedRunId] = React.useState<string | null>(null)

  return (
    <>
      <PageHeader
        title="Reconciliation"
        description="Compares OMS records against Shopify and Shiprocket without ever auto-correcting — mismatches and missing records are reported here for review."
        actions={<TriggerRunButton />}
      />
      <div className="flex flex-col gap-8">
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">Runs</h2>
          <RunsSection selectedRunId={selectedRunId} onSelectRun={setSelectedRunId} />
        </section>
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">
              Results{" "}
              {selectedRunId && (
                <span className="text-muted-foreground font-normal">
                  — filtered to selected run
                </span>
              )}
            </h2>
            {selectedRunId && (
              <Button variant="ghost" size="sm" onClick={() => setSelectedRunId(null)}>
                Clear selection
              </Button>
            )}
          </div>
          <ResultsSection runId={selectedRunId} />
        </section>
      </div>
    </>
  )
}
