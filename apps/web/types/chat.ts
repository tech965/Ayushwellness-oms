export interface ChatTurn {
  role: "user" | "assistant"
  content: string
}

export interface ChatApiResponse {
  answer: string
  ok: boolean
  partial: boolean
  tools_used: string[]
  sources: string[]
  data: Record<string, unknown>
  error_code: string | null
  conversation_id: string | null
  model: string | null
  latency_ms: number | null
  timestamp: string
}

export interface ChatSuggestion {
  label: string
  prompt: string
}

/** A message as held in the panel's local transcript. */
export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
  createdAt: string
  /** assistant-only metadata */
  ok?: boolean
  partial?: boolean
  sources?: string[]
  toolsUsed?: string[]
  errorCode?: string | null
  latencyMs?: number | null
}
