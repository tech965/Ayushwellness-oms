import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { DashboardSummary } from "@/types/dashboard"

async function fetchDashboardSummary(): Promise<DashboardSummary> {
  const response =
    await apiClient.get<ApiResponse<DashboardSummary>>("/dashboard/summary")
  if (!response.data.data) throw new Error("Dashboard summary not available.")
  return response.data.data
}

export function useDashboardSummary() {
  return useQuery({
    queryKey: ["dashboard", "summary"],
    queryFn: fetchDashboardSummary,
  })
}
