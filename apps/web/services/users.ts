import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { User, UserCreateInput, UserUpdateInput } from "@/types/user"

interface ListParams {
  page: number
  pageSize: number
}

async function fetchUsers(params: ListParams): Promise<PaginatedResponse<User>> {
  const response = await apiClient.get<PaginatedResponse<User>>("/users", {
    params: { page: params.page, page_size: params.pageSize },
  })
  return response.data
}

/** Unlike `/orders`, `/customers`, `/products`, `GET /users` has no `q`
 * search param server-side -- the Users page instead fetches one large
 * page (`pageSize` up to the backend's own `page_size<=200` ceiling,
 * matching `useCouriers`' precedent for a small, rarely-changing
 * master-data list of internal accounts) and searches/paginates it
 * client-side. See `app/(dashboard)/users/page.tsx`.
 */
export function useUsers(params: ListParams) {
  return useQuery({
    queryKey: ["users", params],
    queryFn: () => fetchUsers(params),
    placeholderData: (previous) => previous,
  })
}

export function useCreateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: UserCreateInput) => {
      const response = await apiClient.post<ApiResponse<User>>("/users", input)
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })
}

export function useUpdateUser(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: UserUpdateInput) => {
      const response = await apiClient.patch<ApiResponse<User>>(`/users/${id}`, input)
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })
}

/** `DELETE /users/{id}` soft-deactivates server-side -- it never hard-
 * deletes the row (spec §31). "Activate" is a separate `useUpdateUser`
 * call with `is_active: true`; this hook only ever moves a user to
 * inactive.
 */
export function useDeactivateUser() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      const response = await apiClient.delete<ApiResponse<null>>(`/users/${id}`)
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })
}
