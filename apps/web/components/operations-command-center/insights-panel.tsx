import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { PriorityBadge } from "./priority-badge"
import type { CommandCenterInsight } from "@/types/operations-command-center"

/** "What Needs Your Attention" (spec section 8) -- every message here is
 * generated server-side by deterministic business rules over real data
 * (`OperationsCommandCenterService._build_insights`), never an AI call.
 */
export function InsightsPanel({ insights }: { insights: CommandCenterInsight[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>🧠 What Needs Your Attention</CardTitle>
      </CardHeader>
      <CardContent>
        {insights.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No notable insights for this period.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {insights.map((insight, index) => (
              <li key={index} className="flex items-start justify-between gap-3 text-sm">
                <span>{insight.message}</span>
                <PriorityBadge value={insight.priority} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
