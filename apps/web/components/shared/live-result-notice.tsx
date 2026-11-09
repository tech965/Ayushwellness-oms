import { AlertTriangle, Info } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import type { PaginationMeta } from "@/types/api"

interface LiveResultNoticeProps {
  meta: Partial<PaginationMeta> | undefined
}

/** Shown above a list whose rows were read live from a provider. Makes
 * two things explicit that a live read can make untrue: that a total is
 * only an estimate, and that the result set was cut short (so a count of
 * zero or a short list is not proof that nothing else exists).
 * Renders nothing for a normal, exact result.
 */
export function LiveResultNotice({ meta }: LiveResultNoticeProps) {
  if (!meta) return null
  const warnings = meta.warnings ?? []
  const estimate = meta.total_is_estimate === true

  if (warnings.length === 0 && !estimate) return null

  if (warnings.length > 0) {
    return (
      <Alert variant="destructive" data-testid="live-result-warning">
        <AlertTriangle className="size-4" />
        <AlertTitle>Results may be incomplete</AlertTitle>
        <AlertDescription>
          {warnings.map((warning) => (
            <p key={warning}>{warning}</p>
          ))}
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <Alert data-testid="live-result-estimate">
      <Info className="size-4" />
      <AlertTitle>Totals are estimated</AlertTitle>
      <AlertDescription>
        This list is read live from the provider, so the exact number of matching
        records is not known. More pages may be available
        {meta.has_more ? "" : " only if you widen the filters"}.
      </AlertDescription>
    </Alert>
  )
}
