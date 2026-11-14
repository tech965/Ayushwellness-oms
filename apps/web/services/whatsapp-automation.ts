import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type {
  Contact,
  DashboardSummary,
  Group,
  QueuedMessage,
  Schedule,
  ScheduleCreateInput,
  WhatsAppStatus,
} from "@/types/whatsapp-automation"

const BASE = "/whatsapp"

export function useWhatsAppStatus() {
  return useQuery({
    queryKey: ["whatsapp", "status"],
    queryFn: async () =>
      (await apiClient.get<ApiResponse<WhatsAppStatus>>(`${BASE}/status`)).data.data!,
  })
}

export function useContacts() {
  return useQuery({
    queryKey: ["whatsapp", "contacts"],
    queryFn: async () =>
      (await apiClient.get<ApiResponse<Contact[]>>(`${BASE}/contacts`)).data.data!,
  })
}

export function useCreateContact() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: { name: string; phone: string }) =>
      (await apiClient.post<ApiResponse<Contact>>(`${BASE}/contacts`, body)).data.data!,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["whatsapp", "contacts"] }),
  })
}

export function useSetContactOptStatus() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, optedIn }: { id: string; optedIn: boolean }) =>
      (
        await apiClient.post<ApiResponse<Contact>>(
          `${BASE}/contacts/${id}/${optedIn ? "opt-in" : "opt-out"}`
        )
      ).data.data!,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["whatsapp", "contacts"] }),
  })
}

export function useGroups() {
  return useQuery({
    queryKey: ["whatsapp", "groups"],
    queryFn: async () => (await apiClient.get<ApiResponse<Group[]>>(`${BASE}/groups`)).data.data!,
  })
}

export function useCreateGroup() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: { name: string; contact_ids: string[] }) =>
      (await apiClient.post<ApiResponse<Group>>(`${BASE}/groups`, body)).data.data!,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["whatsapp", "groups"] }),
  })
}

export function useSchedules() {
  return useQuery({
    queryKey: ["whatsapp", "schedules"],
    queryFn: async () =>
      (await apiClient.get<ApiResponse<Schedule[]>>(`${BASE}/schedules`)).data.data!,
  })
}

export function useCreateSchedule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: ScheduleCreateInput) =>
      (await apiClient.post<ApiResponse<Schedule>>(`${BASE}/schedules`, body)).data.data!,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["whatsapp", "schedules"] }),
  })
}

export function useUpdateSchedule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: Partial<Schedule> }) =>
      (await apiClient.patch<ApiResponse<Schedule>>(`${BASE}/schedules/${id}`, body)).data.data!,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["whatsapp", "schedules"] }),
  })
}

export function useDeleteSchedule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`${BASE}/schedules/${id}`)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["whatsapp", "schedules"] }),
  })
}

export interface QueueFilters {
  status?: string
  schedule_id?: string
  message_type?: string
  date_from?: string
  date_to?: string
}

export function useQueueHistory(filters: QueueFilters) {
  return useQuery({
    queryKey: ["whatsapp", "queue", filters],
    queryFn: async () =>
      (
        await apiClient.get<ApiResponse<QueuedMessage[]>>(`${BASE}/queue`, { params: filters })
      ).data.data!,
  })
}

export function useCancelQueuedMessage() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (id: string) =>
      (await apiClient.post<ApiResponse<QueuedMessage>>(`${BASE}/queue/${id}/cancel`)).data.data!,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["whatsapp", "queue"] }),
  })
}

export async function downloadQueueExport(filters: QueueFilters): Promise<void> {
  const response = await apiClient.get(`${BASE}/queue/export`, {
    params: filters,
    responseType: "blob",
  })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement("a")
  link.href = url
  link.download = "whatsapp-history.xlsx"
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

export function useWhatsAppDashboard() {
  return useQuery({
    queryKey: ["whatsapp", "dashboard"],
    queryFn: async () =>
      (await apiClient.get<ApiResponse<DashboardSummary>>(`${BASE}/dashboard`)).data.data!,
    refetchInterval: 30_000,
  })
}
