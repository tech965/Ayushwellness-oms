"use client"

import { Info } from "lucide-react"

import { PageHeader } from "@/components/shared/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { useLiveCapabilities } from "@/services/live"

interface LiveUnavailableProps {
  title: string
  /** `operation` key from `GET /analytics/live/capabilities`. */
  operation: string
  /** Used only if the capabilities request itself fails. */
  fallbackReason: string
}

/** Explains why a screen has no live equivalent. Only for functionality
 * the provider APIs genuinely cannot support — never for a feature whose
 * backend exists.
 */
export function LiveUnavailable({ title, operation, fallbackReason }: LiveUnavailableProps) {
  const capabilities = useLiveCapabilities()
  const reason =
    capabilities.data?.find((c) => c.operation === operation)?.reason ?? fallbackReason
  return (
    <>
      <PageHeader title={title} description="Not available while provider data is read live." />
      <Alert data-testid="live-unavailable-reason">
        <Info className="size-4" />
        <AlertTitle>Why this is not shown</AlertTitle>
        <AlertDescription>{reason}</AlertDescription>
      </Alert>
    </>
  )
}
