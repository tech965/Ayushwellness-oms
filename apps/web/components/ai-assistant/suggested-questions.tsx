"use client"

import { Sparkles } from "lucide-react"

import { cn } from "@/lib/utils"
import type { ChatSuggestion } from "@/types/chat"

interface SuggestedQuestionsProps {
  suggestions: ChatSuggestion[]
  onPick: (prompt: string) => void
  disabled?: boolean
  className?: string
}

export function SuggestedQuestions({
  suggestions,
  onPick,
  disabled,
  className,
}: SuggestedQuestionsProps) {
  if (suggestions.length === 0) return null

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <p className="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
        <Sparkles className="size-3.5" />
        Try asking
      </p>
      <div className="flex flex-wrap gap-2">
        {suggestions.map((s) => (
          <button
            key={s.label}
            type="button"
            disabled={disabled}
            onClick={() => onPick(s.prompt)}
            className="border-border bg-background hover:bg-muted text-foreground rounded-full border px-3 py-1.5 text-xs transition-colors disabled:pointer-events-none disabled:opacity-50"
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  )
}
