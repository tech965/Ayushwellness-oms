import { useMutation, useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { AmazonStatus, ChunkRef, PollResponse } from "@/types/amazon-sales"

const BASE = "/marketplaces/amazon/sales"

export function useAmazonStatus() {
  return useQuery({
    queryKey: ["amazon-sales", "status"],
    queryFn: async () =>
      (await apiClient.get<ApiResponse<AmazonStatus>>(`${BASE}/status`)).data.data!,
  })
}

export async function startAmazonReports(
  date_from: string,
  date_to: string
): Promise<ChunkRef[]> {
  const r = await apiClient.post<ApiResponse<ChunkRef[]>>(`${BASE}/reports`, {
    date_from,
    date_to,
  })
  return r.data.data ?? []
}

/** Stateless polling: the server keeps nothing; the browser holds Amazon's
 * report ids and re-asks until every report is DONE. */
export function useAmazonReportPoll(chunks: ChunkRef[] | null) {
  return useQuery({
    queryKey: ["amazon-sales", "poll", chunks?.map((c) => c.report_id)],
    enabled: !!chunks,
    queryFn: async () =>
      (
        await apiClient.post<ApiResponse<PollResponse>>(`${BASE}/reports/poll`, {
          chunks,
        })
      ).data.data!,
    refetchInterval: (query) => (query.state.data?.state === "processing" ? 5000 : false),
    retry: false,
    refetchOnWindowFocus: false,
  })
}

export function useAmazonExport() {
  return useMutation({
    mutationFn: async (chunks: ChunkRef[]) => {
      const response = await apiClient.post(
        `${BASE}/reports/export`,
        { chunks },
        { responseType: "blob" }
      )
      const url = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement("a")
      link.href = url
      link.download = "amazon-sales.xlsx"
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    },
  })
}
