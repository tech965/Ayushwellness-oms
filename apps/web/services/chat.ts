import { useMutation, useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { ChatApiResponse, ChatSuggestion, ChatTurn } from "@/types/chat"

export interface SendChatInput {
  message: string
  history: ChatTurn[]
  conversationId?: string | null
}

async function sendChat(input: SendChatInput): Promise<ChatApiResponse> {
  // The chat endpoint returns success:false for "answered but couldn't
  // fully retrieve" cases (not_configured / all_tools_failed) with a
  // usable `data` payload — so read `data` regardless of the envelope's
  // success flag and let the caller branch on `data.ok`.
  const response = await apiClient.post<ApiResponse<ChatApiResponse>>("/chat", {
    message: input.message,
    history: input.history,
    conversation_id: input.conversationId ?? undefined,
  })
  const data = response.data.data
  if (!data) {
    throw new Error(response.data.message || "The assistant did not return a response.")
  }
  return data
}

export function useSendChat() {
  return useMutation({ mutationFn: sendChat })
}

async function fetchChatSuggestions(): Promise<ChatSuggestion[]> {
  const response = await apiClient.get<ApiResponse<{ suggestions: ChatSuggestion[] }>>(
    "/chat/suggestions"
  )
  return response.data.data?.suggestions ?? []
}

export function useChatSuggestions(enabled = true) {
  return useQuery({
    queryKey: ["chat", "suggestions"],
    queryFn: fetchChatSuggestions,
    enabled,
    staleTime: 5 * 60_000,
  })
}
