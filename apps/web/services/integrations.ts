import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  Integration,
  IntegrationHealth,
  SyncJob,
  SyncType,
  WebhookEvent,
} from "@/types/integration"

interface ListParams {
  page: number
  pageSize: number
}

async function fetchIntegrations(
  params: ListParams
): Promise<PaginatedResponse<Integration>> {
  const response = await apiClient.get<PaginatedResponse<Integration>>("/integrations", {
    params: { page: params.page, page_size: params.pageSize },
  })
  return response.data
}

export function useIntegrations(params: ListParams) {
  return useQuery({
    queryKey: ["integrations", params],
    queryFn: () => fetchIntegrations(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchIntegration(id: string): Promise<Integration> {
  const response = await apiClient.get<ApiResponse<Integration>>(`/integrations/${id}`)
  if (!response.data.data) throw new Error("Integration not found.")
  return response.data.data
}

export function useIntegration(id: string) {
  return useQuery({
    queryKey: ["integrations", id],
    queryFn: () => fetchIntegration(id),
    enabled: Boolean(id),
  })
}

async function fetchIntegrationHealth(id: string): Promise<IntegrationHealth> {
  const response = await apiClient.get<ApiResponse<IntegrationHealth>>(
    `/integrations/${id}/health`
  )
  if (!response.data.data) throw new Error("Health status unavailable.")
  return response.data.data
}

export function useIntegrationHealth(id: string) {
  return useQuery({
    queryKey: ["integrations", id, "health"],
    queryFn: () => fetchIntegrationHealth(id),
    enabled: Boolean(id),
  })
}

export function useRunHealthCheck(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<ApiResponse<IntegrationHealth>>(
        `/integrations/${id}/health-check`
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["integrations", id] })
    },
  })
}

async function fetchIntegrationSyncHistory(
  id: string,
  params: ListParams
): Promise<PaginatedResponse<SyncJob>> {
  const response = await apiClient.get<PaginatedResponse<SyncJob>>(
    `/integrations/${id}/sync-history`,
    { params: { page: params.page, page_size: params.pageSize } }
  )
  return response.data
}

export function useIntegrationSyncHistory(id: string, params: ListParams) {
  return useQuery({
    queryKey: ["integrations", id, "sync-history", params],
    queryFn: () => fetchIntegrationSyncHistory(id, params),
    enabled: Boolean(id),
    placeholderData: (previous) => previous,
  })
}

export function useTriggerSync(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { entityType: string; syncType?: SyncType }) => {
      const response = await apiClient.post<ApiResponse<SyncJob>>(`/sync/${id}/trigger`, {
        entity_type: input.entityType,
        sync_type: input.syncType ?? "incremental",
      })
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["integrations", id] })
    },
  })
}

interface SyncJobListParams extends ListParams {
  integrationId?: string
}

async function fetchSyncJobs(
  params: SyncJobListParams
): Promise<PaginatedResponse<SyncJob>> {
  const response = await apiClient.get<PaginatedResponse<SyncJob>>("/sync-jobs", {
    params: { page: params.page, page_size: params.pageSize },
  })
  return response.data
}

export function useSyncJobs(params: SyncJobListParams) {
  return useQuery({
    queryKey: ["sync-jobs", params],
    queryFn: () => fetchSyncJobs(params),
    placeholderData: (previous) => previous,
  })
}

interface WebhookEventListParams extends ListParams {
  integrationId?: string
}

async function fetchWebhookEvents(
  params: WebhookEventListParams
): Promise<PaginatedResponse<WebhookEvent>> {
  const response = await apiClient.get<PaginatedResponse<WebhookEvent>>(
    "/webhook-events",
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        integration_id: params.integrationId || undefined,
      },
    }
  )
  return response.data
}

export function useWebhookEvents(params: WebhookEventListParams) {
  return useQuery({
    queryKey: ["webhook-events", params],
    queryFn: () => fetchWebhookEvents(params),
    placeholderData: (previous) => previous,
  })
}
