"use client"

import { AlertTriangle, Database, RotateCcw } from "lucide-react"

import { cn } from "@/lib/utils"
import { formatDateTime } from "@/lib/format"
import { Button } from "@/components/ui/button"
import type { ChatMessage } from "@/types/chat"

interface MessageListProps {
  messages: ChatMessage[]
  onRetry?: () => void
  retryDisabled?: boolean
}

export function MessageList({ messages, onRetry, retryDisabled }: MessageListProps) {
  return (
    <div className="flex flex-col gap-4">
      {messages.map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          onRetry={onRetry}
          retryDisabled={retryDisabled}
        />
      ))}
    </div>
  )
}

function MessageBubble({
  message,
  onRetry,
  retryDisabled,
}: {
  message: ChatMessage
  onRetry?: () => void
  retryDisabled?: boolean
}) {
  const isUser = message.role === "user"
  const failed = message.role === "assistant" && message.ok === false

  return (
    <div className={cn("flex flex-col gap-1", isUser ? "items-end" : "items-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm whitespace-pre-wrap",
          isUser
            ? "bg-primary text-primary-foreground rounded-br-sm"
            : failed
              ? "bg-destructive/10 text-foreground border-destructive/30 rounded-bl-sm border"
              : "bg-muted text-foreground rounded-bl-sm"
        )}
      >
        {message.content}
      </div>

      {message.role === "assistant" && (
        <div className="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
          <span>{formatDateTime(message.createdAt)}</span>

          {message.partial && (
            <span className="text-amber-600 dark:text-amber-500">
              <AlertTriangle className="mr-0.5 inline size-3" />
              partial data
            </span>
          )}

          {message.sources && message.sources.length > 0 && (
            <span className="inline-flex items-center gap-1">
              <Database className="size-3" />
              {message.sources.join(" · ")}
            </span>
          )}

          {message.toolsUsed && message.toolsUsed.length > 0 && (
            <span className="opacity-70">
              {message.toolsUsed.map((t) => t.replace(/_/g, " ")).join(", ")}
            </span>
          )}

          {typeof message.latencyMs === "number" && (
            <span className="opacity-60">{(message.latencyMs / 1000).toFixed(1)}s</span>
          )}

          {failed && onRetry && (
            <Button
              type="button"
              size="xs"
              variant="ghost"
              onClick={onRetry}
              disabled={retryDisabled}
            >
              <RotateCcw className="size-3" />
              Retry
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
