import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { PaginatedResponse } from "@/types/api"
import type { AuditLog, AuditLogListFilters } from "@/types/audit-log"

interface ListParams extends AuditLogListFilters {
  page: number
  pageSize: number
}

async function fetchAuditLogs(params: ListParams): Promise<PaginatedResponse<AuditLog>> {
  const response = await apiClient.get<PaginatedResponse<AuditLog>>("/audit-logs", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      entity_type: params.entity_type || undefined,
      entity_id: params.entity_id || undefined,
      user_id: params.user_id || undefined,
      action: params.action || undefined,
      date_from: params.date_from,
      date_to: params.date_to,
    },
  })
  return response.data
}

export function useAuditLogs(params: ListParams) {
  return useQuery({
    queryKey: ["audit-logs", params],
    queryFn: () => fetchAuditLogs(params),
    placeholderData: (previous) => previous,
  })
}
