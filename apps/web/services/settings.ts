import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { AppSettingsResponse, AppSettingsUpdateRequest } from "@/types/settings"

const SETTINGS_QUERY_KEY = ["settings"] as const

async function fetchSettings(): Promise<AppSettingsResponse> {
  const response = await apiClient.get<ApiResponse<AppSettingsResponse>>("/settings")
  if (!response.data.data) throw new Error("Settings not available.")
  return response.data.data
}

/** Org-wide OMS settings (Administration -> Settings). A long `staleTime`
 * is safe -- this changes only when someone explicitly saves a section,
 * and `useUpdateSettings` below invalidates this same key on success.
 */
export function useSettings() {
  return useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: fetchSettings,
    staleTime: 60_000,
  })
}

export function useUpdateSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (payload: AppSettingsUpdateRequest) => {
      const response = await apiClient.put<ApiResponse<AppSettingsResponse>>(
        "/settings",
        payload
      )
      if (!response.data.data) throw new Error("Settings update failed.")
      return response.data.data
    },
    onSuccess: (data) => {
      queryClient.setQueryData(SETTINGS_QUERY_KEY, data)
    },
  })
}
