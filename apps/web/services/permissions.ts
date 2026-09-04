import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { Permission } from "@/types/permission"

async function fetchPermissions(): Promise<Permission[]> {
  const response = await apiClient.get<ApiResponse<Permission[]>>("/permissions")
  return response.data.data ?? []
}

/** Read-only permission catalog (no create/update/delete -- the backend
 * doesn't expose any) used to populate the role create/edit UI. A small,
 * effectively static list, same as `useCouriers`, so a longer `staleTime`
 * avoids refetching it every time a role dialog opens.
 */
export function usePermissions() {
  return useQuery({
    queryKey: ["permissions"],
    queryFn: fetchPermissions,
    staleTime: 5 * 60 * 1000,
  })
}
