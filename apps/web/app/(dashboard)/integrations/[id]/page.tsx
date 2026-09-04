"use client"

import * as React from "react"
import { useParams } from "next/navigation"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime } from "@/lib/format"
import { useAuth } from "@/lib/auth-context"
import { usePaginationState } from "@/lib/use-pagination"
import {
  useIntegration,
  useIntegrationHealth,
  useIntegrationSyncHistory,
  useRunHealthCheck,
  useTriggerSync,
  useWebhookEvents,
} from "@/services/integrations"
import type { SyncJob, SyncType, WebhookEvent } from "@/types/integration"

const SYNC_ENTITY_TYPES_BY_CODE: Record<string, { label: string; value: string }[]> = {
  shopify: [
    { label: "Sync Customers", value: "customers" },
    { label: "Sync Products", value: "products" },
    { label: "Sync Orders", value: "orders" },
  ],
  // "Sync Shipments" pulls the account's existing Shiprocket shipments
  // (GET /shipments) into the OMS -- distinct from shipment *creation*,
  // which stays a per-order push action (see the order detail page's
  // "Ship via Shiprocket" button). This entity has been fully supported
  // by the backend (app.integrations.shiprocket.adapter's `_FETCH_ROUTES`,
  // entity_sync._upsert_shipment) since it was built, but was missing
  // from this list -- Full Sync silently never triggered it. RTO still
  // has no separate sync path: RTO records are derived automatically as
  // a side effect of tracking refresh (see docs/integrations/shiprocket.md),
  // which requires shipments to already be pulled in for NDR/RTO matching.
  shiprocket: [
    { label: "Sync Shipments", value: "shipments" },
    { label: "Sync Tracking", value: "tracking" },
    { label: "Sync NDR", value: "ndr" },
  ],
}

export default function IntegrationDetailPage() {
  const params = useParams<{ id: string }>()
  const integrationId = params.id
  const { hasPermission } = useAuth()

  const integrationQuery = useIntegration(integrationId)
  const healthQuery = useIntegrationHealth(integrationId)
  const runHealthCheck = useRunHealthCheck(integrationId)

  const syncHistory = usePaginationState()
  const syncHistoryQuery = useIntegrationSyncHistory(integrationId, {
    page: syncHistory.page,
    pageSize: syncHistory.pageSize,
  })

  const webhookEvents = usePaginationState()
  const webhookEventsQuery = useWebhookEvents({
    integrationId,
    page: webhookEvents.page,
    pageSize: webhookEvents.pageSize,
  })

  const syncJobColumns: DataTableColumn<SyncJob>[] = [
    { id: "entity_type", header: "Entity", cell: (j) => j.entity_type },
    { id: "sync_type", header: "Type", cell: (j) => j.sync_type },
    {
      id: "status",
      header: "Status",
      cell: (j) => <StatusBadge domain="sync_job" status={j.status} />,
    },
    {
      id: "records",
      header: "Created / Updated / Failed",
      cell: (j) => `${j.records_created} / ${j.records_updated} / ${j.records_failed}`,
    },
    { id: "errors", header: "Errors", cell: (j) => j.error_count },
    {
      id: "started",
      header: "Started",
      cell: (j) => (j.started_at ? formatDateTime(j.started_at) : "—"),
    },
    {
      id: "completed",
      header: "Completed",
      cell: (j) => (j.completed_at ? formatDateTime(j.completed_at) : "—"),
    },
  ]

  const webhookEventColumns: DataTableColumn<WebhookEvent>[] = [
    { id: "event_type", header: "Event Type", cell: (e) => e.event_type },
    {
      id: "external_event_id",
      header: "External Event ID",
      cell: (e) => e.external_event_id,
    },
    {
      id: "status",
      header: "Status",
      cell: (e) => <StatusBadge domain="webhook_event" status={e.status} />,
    },
    { id: "retry_count", header: "Retries", cell: (e) => e.retry_count },
    { id: "received", header: "Received", cell: (e) => formatDateTime(e.received_at) },
    {
      id: "error",
      header: "Error",
      cell: (e) => e.error_message ?? "—",
    },
  ]

  return (
    <>
      <PageHeader
        title={integrationQuery.data?.name ?? "Integration"}
        description={`Code: ${integrationQuery.data?.code ?? integrationId}`}
        actions={
          hasPermission("integrations.test") && (
            <Button
              variant="outline"
              size="sm"
              disabled={runHealthCheck.isPending}
              onClick={() => {
                runHealthCheck.mutate(undefined, {
                  onSuccess: () => toast.success("Test connection complete."),
                  onError: (error) => toast.error(getApiErrorMessage(error)),
                })
              }}
            >
              {runHealthCheck.isPending ? "Testing..." : "Test Connection"}
            </Button>
          )
        }
      />

      <QueryStates
        isLoading={integrationQuery.isLoading}
        isError={integrationQuery.isError}
        error={integrationQuery.error}
        data={integrationQuery.data}
        onRetry={() => void integrationQuery.refetch()}
      >
        {(integration) => (
          <div className="flex flex-col gap-4">
            <Card>
              <CardHeader className="flex flex-row items-center gap-2">
                <StatusBadge domain="integration" status={integration.status} />
                <CardTitle className="text-muted-foreground text-sm font-normal">
                  {integration.enabled ? "Enabled" : "Not connected"}
                </CardTitle>
              </CardHeader>
              <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat label="Type" value={integration.type} />
                <Stat
                  label="Last sync"
                  value={
                    integration.last_sync_at
                      ? formatDateTime(integration.last_sync_at)
                      : "Never"
                  }
                />
                <Stat
                  label="Last successful sync"
                  value={
                    integration.last_successful_sync_at
                      ? formatDateTime(integration.last_successful_sync_at)
                      : "—"
                  }
                />
                <Stat
                  label="Last failure"
                  value={
                    integration.last_failure_at
                      ? formatDateTime(integration.last_failure_at)
                      : "—"
                  }
                />
                {healthQuery.data?.error_message && (
                  <Stat label="Last error" value={healthQuery.data.error_message} span />
                )}
              </CardContent>
            </Card>

            {hasPermission("sync_jobs.manage") && (
              <SyncActions integrationId={integrationId} code={integration.code} />
            )}

            <Tabs defaultValue="sync-history">
              <TabsList>
                <TabsTrigger value="sync-history">Sync History</TabsTrigger>
                <TabsTrigger value="webhook-events">Webhook Events</TabsTrigger>
              </TabsList>

              <TabsContent value="sync-history" className="flex flex-col gap-4">
                <QueryStates
                  isLoading={syncHistoryQuery.isLoading}
                  isError={syncHistoryQuery.isError}
                  error={syncHistoryQuery.error}
                  data={syncHistoryQuery.data}
                  onRetry={() => void syncHistoryQuery.refetch()}
                  isEmpty={(data) => data.data.length === 0}
                  emptyTitle="No sync jobs yet"
                  emptyDescription="Trigger a sync above, or wait for a scheduled one, to see history here."
                >
                  {(data) => (
                    <>
                      <DataTable
                        columns={syncJobColumns}
                        data={data.data}
                        rowKey={(j) => j.id}
                      />
                      <PaginationBar
                        meta={data.meta}
                        onPageChange={syncHistory.setPage}
                      />
                    </>
                  )}
                </QueryStates>
              </TabsContent>

              <TabsContent value="webhook-events" className="flex flex-col gap-4">
                <QueryStates
                  isLoading={webhookEventsQuery.isLoading}
                  isError={webhookEventsQuery.isError}
                  error={webhookEventsQuery.error}
                  data={webhookEventsQuery.data}
                  onRetry={() => void webhookEventsQuery.refetch()}
                  isEmpty={(data) => data.data.length === 0}
                  emptyTitle="No webhook events yet"
                  emptyDescription="Webhook events appear here once Shopify starts delivering them to this integration."
                >
                  {(data) => (
                    <>
                      <DataTable
                        columns={webhookEventColumns}
                        data={data.data}
                        rowKey={(e) => e.id}
                      />
                      <PaginationBar
                        meta={data.meta}
                        onPageChange={webhookEvents.setPage}
                      />
                    </>
                  )}
                </QueryStates>
              </TabsContent>
            </Tabs>
          </div>
        )}
      </QueryStates>
    </>
  )
}

export function SyncActions({ integrationId, code }: { integrationId: string; code: string }) {
  const triggerSync = useTriggerSync(integrationId)
  const [pendingEntity, setPendingEntity] = React.useState<string | null>(null)
  const entityTypes = SYNC_ENTITY_TYPES_BY_CODE[code] ?? []

  function trigger(entityType: string, syncType: SyncType = "full") {
    setPendingEntity(entityType)
    triggerSync.mutate(
      { entityType, syncType },
      {
        onSuccess: () => toast.success(`${entityType} sync queued.`),
        onError: (error) => toast.error(getApiErrorMessage(error)),
        onSettled: () => setPendingEntity(null),
      }
    )
  }

  async function triggerFullSync() {
    setPendingEntity("full")
    // Each entity is queued independently -- one entity failing to queue
    // (e.g. a sync already active for it) must never stop the rest from
    // being triggered, matching how a failure inside one entity's sync
    // job already never aborts another entity's job on the backend.
    const failures: string[] = []
    for (const { value } of entityTypes) {
      try {
        await triggerSync.mutateAsync({ entityType: value, syncType: "full" })
      } catch (error) {
        failures.push(`${value}: ${getApiErrorMessage(error)}`)
      }
    }
    setPendingEntity(null)
    if (failures.length === 0) {
      toast.success("Full sync queued.")
    } else if (failures.length < entityTypes.length) {
      toast.warning(`Full sync partially queued. ${failures.join("; ")}`)
    } else {
      toast.error(`Full sync failed to queue. ${failures.join("; ")}`)
    }
  }

  if (entityTypes.length === 0) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Sync</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-wrap gap-2">
        {entityTypes.map((entity) => (
          <Button
            key={entity.value}
            variant="outline"
            size="sm"
            disabled={pendingEntity !== null}
            onClick={() => trigger(entity.value)}
          >
            {pendingEntity === entity.value ? "Queuing..." : entity.label}
          </Button>
        ))}
        <Button
          variant="default"
          size="sm"
          disabled={pendingEntity !== null}
          onClick={() => void triggerFullSync()}
        >
          {pendingEntity === "full" ? "Queuing full sync..." : "Full Sync"}
        </Button>
      </CardContent>
    </Card>
  )
}

function Stat({ label, value, span }: { label: string; value: string; span?: boolean }) {
  return (
    <div className={span ? "col-span-full" : undefined}>
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="text-sm font-medium break-words">{value}</p>
    </div>
  )
}
