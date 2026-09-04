export function TypingIndicator() {
  return (
    <div
      className="text-muted-foreground flex items-center gap-1.5 text-sm"
      role="status"
      aria-label="Assistant is thinking"
    >
      <span className="bg-muted-foreground/60 size-1.5 animate-bounce rounded-full [animation-delay:-0.3s]" />
      <span className="bg-muted-foreground/60 size-1.5 animate-bounce rounded-full [animation-delay:-0.15s]" />
      <span className="bg-muted-foreground/60 size-1.5 animate-bounce rounded-full" />
      <span className="ml-1">Checking OMS data…</span>
    </div>
  )
}
