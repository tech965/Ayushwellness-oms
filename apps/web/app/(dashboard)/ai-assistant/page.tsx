"use client"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { PageHeader } from "@/components/shared/page-header"
import { ChatPanel } from "@/components/ai-assistant/chat-panel"
import { useAuth } from "@/lib/auth-context"

/**
 * Standalone AI assistant route. Intentionally NOT wired into the sidebar
 * nav yet (see project plan / PHASE 20) — reach it directly at
 * `/ai-assistant`. It reuses the dashboard route group's `AuthProvider`
 * guard, so an unauthenticated visitor is bounced to `/login` before
 * anything here renders.
 */
export default function AiAssistantPage() {
  const { hasPermission, isLoading } = useAuth()

  if (isLoading) return null

  if (!hasPermission("chat.use")) {
    return (
      <>
        <PageHeader
          title="OMS AI Assistant"
          description="Ask questions about live OMS operations in plain language."
        />
        <Alert>
          <AlertTitle>No access</AlertTitle>
          <AlertDescription>
            Your role doesn&apos;t include access to the OMS AI Assistant. Ask an
            administrator to grant the <code>chat.use</code> permission.
          </AlertDescription>
        </Alert>
      </>
    )
  }

  return (
    <>
      <PageHeader
        title="OMS AI Assistant"
        description="Natural-language answers from live Shopify & Shiprocket data synced into the OMS. Every number comes from a real query — the assistant never guesses."
      />
      <ChatPanel />
    </>
  )
}
