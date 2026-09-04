import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { Role, RoleCreateInput, RoleUpdateInput } from "@/types/role"

async function fetchRoles(): Promise<Role[]> {
  const response = await apiClient.get<ApiResponse<Role[]>>("/roles")
  return response.data.data ?? []
}

/** `GET /roles` returns every role as one plain array -- unlike `/users`,
 * this endpoint has no pagination `meta` at all (role counts are small by
 * nature: a handful of named roles, not a growing business entity).
 */
export function useRoles() {
  return useQuery({
    queryKey: ["roles"],
    queryFn: fetchRoles,
  })
}

export function useCreateRole() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: RoleCreateInput) => {
      const response = await apiClient.post<ApiResponse<Role>>("/roles", input)
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["roles"] })
    },
  })
}

/** `PATCH /roles/{id}` only ever accepts `description`/`permission_ids`
 * server-side (`RoleUpdateRequest` has no `name` field) -- `name` is
 * immutable after creation, enforced here simply by `RoleUpdateInput`
 * never having a `name` to send.
 */
export function useUpdateRole(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: RoleUpdateInput) => {
      const response = await apiClient.patch<ApiResponse<Role>>(`/roles/${id}`, input)
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["roles"] })
    },
  })
}
