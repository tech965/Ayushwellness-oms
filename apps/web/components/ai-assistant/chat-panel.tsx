"use client"

import * as React from "react"
import { Bot, SendHorizontal, Trash2 } from "lucide-react"

import { cn } from "@/lib/utils"
import { getApiErrorMessage } from "@/lib/api-client"
import { useLocalStorageState } from "@/lib/use-local-storage-state"
import { useMounted } from "@/lib/use-mounted"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { useChatSuggestions, useSendChat } from "@/services/chat"
import type { ChatMessage, ChatSuggestion, ChatTurn } from "@/types/chat"

import { MessageList } from "./message-list"
import { SuggestedQuestions } from "./suggested-questions"
import { TypingIndicator } from "./typing-indicator"

const TRANSCRIPT_KEY = "oms_ai_assistant_transcript_v1"
const CONVERSATION_KEY = "oms_ai_assistant_conversation_v1"
const HISTORY_LIMIT = 20

const FALLBACK_SUGGESTIONS: ChatSuggestion[] = [
  { label: "Today's orders", prompt: "How many orders did we receive today?" },
  { label: "Today's revenue", prompt: "What is today's revenue?" },
  { label: "COD vs prepaid", prompt: "What is our COD vs prepaid split today?" },
  { label: "Top products today", prompt: "Give me today's top 5 products." },
  { label: "Compare today vs yesterday", prompt: "Compare today's orders and revenue with yesterday." },
  { label: "Operations digest", prompt: "Give me the most important problems I should look at today." },
]

function newId(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`
  }
}

export function ChatPanel() {
  const mounted = useMounted()
  const [messages, setMessages] = useLocalStorageState<ChatMessage[]>(TRANSCRIPT_KEY, [])
  const [conversationId, setConversationId] = useLocalStorageState<string | null>(
    CONVERSATION_KEY,
    null
  )
  const [input, setInput] = React.useState("")

  const sendChat = useSendChat()
  const suggestionsQuery = useChatSuggestions(mounted)
  const suggestions =
    suggestionsQuery.data && suggestionsQuery.data.length > 0
      ? suggestionsQuery.data
      : FALLBACK_SUGGESTIONS

  const scrollRef = React.useRef<HTMLDivElement>(null)
  React.useEffect(() => {
    const node = scrollRef.current
    // `scrollTo` is unimplemented in jsdom — guard so tests don't throw.
    node?.scrollTo?.({ top: node.scrollHeight, behavior: "smooth" })
  }, [messages, sendChat.isPending])

  const buildHistory = React.useCallback(
    (current: ChatMessage[]): ChatTurn[] =>
      current
        .filter((m) => !(m.role === "assistant" && m.ok === false))
        .slice(-HISTORY_LIMIT)
        .map((m) => ({ role: m.role, content: m.content })),
    []
  )

  const submit = React.useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || sendChat.isPending) return

      const userMessage: ChatMessage = {
        id: newId(),
        role: "user",
        content: trimmed,
        createdAt: new Date().toISOString(),
      }
      const withUser = [...messages, userMessage]
      setMessages(withUser)
      setInput("")

      try {
        const res = await sendChat.mutateAsync({
          message: trimmed,
          history: buildHistory(messages),
          conversationId,
        })
        if (res.conversation_id) setConversationId(res.conversation_id)
        setMessages([
          ...withUser,
          {
            id: newId(),
            role: "assistant",
            content: res.answer,
            createdAt: res.timestamp ?? new Date().toISOString(),
            ok: res.ok,
            partial: res.partial,
            sources: res.sources,
            toolsUsed: res.tools_used,
            errorCode: res.error_code,
            latencyMs: res.latency_ms,
          },
        ])
      } catch (error) {
        setMessages([
          ...withUser,
          {
            id: newId(),
            role: "assistant",
            content: getApiErrorMessage(error),
            createdAt: new Date().toISOString(),
            ok: false,
            errorCode: "request_failed",
          },
        ])
      }
    },
    [
      messages,
      conversationId,
      sendChat,
      buildHistory,
      setMessages,
      setConversationId,
    ]
  )

  const retryLast = React.useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user")
    if (!lastUser) return
    // Drop everything after (and including) any trailing failed reply.
    const trimmed = [...messages]
    while (
      trimmed.length &&
      trimmed[trimmed.length - 1].role === "assistant" &&
      trimmed[trimmed.length - 1].ok === false
    ) {
      trimmed.pop()
    }
    // Also drop the last user message — submit() re-adds it.
    if (trimmed[trimmed.length - 1]?.id === lastUser.id) trimmed.pop()
    setMessages(trimmed)
    void submit(lastUser.content)
  }, [messages, setMessages, submit])

  const clear = React.useCallback(() => {
    setMessages([])
    setConversationId(null)
    sendChat.reset()
  }, [setMessages, setConversationId, sendChat])

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      void submit(input)
    }
  }

  const isEmpty = messages.length === 0

  return (
    <div className="border-border bg-card flex h-[calc(100dvh-11rem)] min-h-[420px] flex-col overflow-hidden rounded-xl border">
      <div className="border-border flex items-center justify-between border-b px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Bot className="text-primary size-4" />
          OMS Assistant
        </div>
        {!isEmpty && (
          <Button type="button" size="xs" variant="ghost" onClick={clear}>
            <Trash2 className="size-3.5" />
            Clear
          </Button>
        )}
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4">
        {isEmpty ? (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <div className="bg-primary/10 text-primary flex size-11 items-center justify-center rounded-full">
              <Bot className="size-5" />
            </div>
            <div className="max-w-sm">
              <p className="text-foreground text-sm font-medium">
                Ask about your live OMS data
              </p>
              <p className="text-muted-foreground mt-1 text-xs">
                Orders, revenue, COD/prepaid, shipments, NDR/RTO, returns, products and
                courier performance — answered from Shopify &amp; Shiprocket data synced
                into the OMS. Dates use IST.
              </p>
            </div>
            <SuggestedQuestions
              suggestions={suggestions}
              onPick={(prompt) => void submit(prompt)}
              disabled={sendChat.isPending}
            />
          </div>
        ) : (
          <>
            <MessageList
              messages={messages}
              onRetry={retryLast}
              retryDisabled={sendChat.isPending}
            />
            {sendChat.isPending && (
              <div className="mt-4">
                <TypingIndicator />
              </div>
            )}
          </>
        )}
      </div>

      <div className="border-border border-t p-3">
        {!isEmpty && (
          <SuggestedQuestions
            suggestions={suggestions.slice(0, 4)}
            onPick={(prompt) => void submit(prompt)}
            disabled={sendChat.isPending}
            className="mb-2.5"
          />
        )}
        <div className="flex items-end gap-2">
          <Textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder="Ask a question about today's operations…"
            aria-label="Message the OMS assistant"
            className={cn("max-h-32 min-h-9 resize-none")}
            disabled={sendChat.isPending}
          />
          <Button
            type="button"
            size="icon"
            onClick={() => void submit(input)}
            disabled={sendChat.isPending || input.trim().length === 0}
            aria-label="Send"
          >
            <SendHorizontal className="size-4" />
          </Button>
        </div>
        <p className="text-muted-foreground mt-1.5 text-[11px]">
          Answers come from real OMS data. The assistant won&apos;t guess numbers — if a
          figure can&apos;t be retrieved it will say so.
        </p>
      </div>
    </div>
  )
}
