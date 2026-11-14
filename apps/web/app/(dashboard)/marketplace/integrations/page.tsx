"use client"

import { useRouter } from "next/navigation"

import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatDateTime } from "@/lib/format"
import { useIntegrations } from "@/services/integrations"

const MARKETPLACE_CODES = ["amazon", "flipkart", "meesho"]

export default function MarketplaceIntegrationsPage() {
  const router = useRouter()
  // Only ~12 Integration rows exist total (one per known provider, see
  // `scripts/seed.py::INTEGRATIONS`) -- filtered client-side to the 3
  // marketplace codes rather than adding a server-side `code` filter the
  // general `/integrations` list doesn't otherwise need.
  const query = useIntegrations({ page: 1, pageSize: 50 })

  return (
    <>
      <PageHeader
        title="Marketplace Integrations"
        description="Amazon, Flipkart, and Meesho connection status — each configured independently of Shopify."
      />
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
        isEmpty={(data) =>
          data.data.filter((i) => MARKETPLACE_CODES.includes(i.code)).length === 0
        }
        emptyTitle="No marketplace integrations found"
        emptyDescription="Run the seed script to create the Amazon/Flipkart/Meesho integration rows."
      >
        {(data) => {
          const rows = data.data.filter((i) => MARKETPLACE_CODES.includes(i.code))
          return (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              {rows.map((integration) => (
                <Card
                  key={integration.id}
                  className="hover:border-primary/40 cursor-pointer transition-colors"
                  onClick={() => router.push(`/integrations/${integration.id}`)}
                >
                  <CardHeader>
                    <CardTitle className="flex items-center justify-between">
                      {integration.name}
                      <StatusBadge domain="integration" status={integration.status} />
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="text-muted-foreground flex flex-col gap-1 text-sm">
                    <p>Enabled: {integration.enabled ? "Yes" : "No"}</p>
                    <p>
                      Last sync:{" "}
                      {integration.last_sync_at
                        ? formatDateTime(integration.last_sync_at)
                        : "Never"}
                    </p>
                    {integration.last_failure_message && (
                      <p className="text-destructive">{integration.last_failure_message}</p>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          )
        }}
      </QueryStates>
    </>
  )
}
