import { Construction } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"

/**
 * Standard empty state for modules not yet implemented. The roadmap phase
 * is sourced from docs/roadmap.md so this stays a pointer, not a
 * duplicate source of truth.
 */
export function PhasePlaceholder({
  module,
  phase,
  description,
}: {
  module: string
  phase: string
  description: string
}) {
  return (
    <Card className="border-dashed">
      <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
        <div className="bg-muted flex size-12 items-center justify-center rounded-full">
          <Construction className="text-muted-foreground size-6" />
        </div>
        <div className="flex items-center gap-2">
          <h2 className="text-foreground text-base font-semibold">{module}</h2>
          <Badge variant="secondary">{phase}</Badge>
        </div>
        <p className="text-muted-foreground max-w-md text-sm">{description}</p>
      </CardContent>
    </Card>
  )
}
