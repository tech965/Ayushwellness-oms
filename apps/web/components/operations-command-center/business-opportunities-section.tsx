import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { BusinessOpportunity } from "@/types/operations-command-center"

const OPPORTUNITY_EMOJI: Record<string, string> = {
  top_revenue_state: "💰",
  fastest_growing_state: "📈",
  emerging_market: "🌱",
}

/** Reuses India Supply Intelligence's own state-level insights
 * (`SupplyIntelligenceService.get_supply_intelligence`) -- see
 * `OperationsCommandCenterService._build_opportunities`. Never a second
 * computation of state growth/revenue.
 */
export function BusinessOpportunitiesSection({
  opportunities,
}: {
  opportunities: BusinessOpportunity[]
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>📈 Business Opportunities</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {opportunities.map((opportunity) => (
          <div key={opportunity.type} className="rounded-lg border p-4">
            <p className="mb-1 flex items-center gap-1.5 text-sm font-semibold">
              <span>{OPPORTUNITY_EMOJI[opportunity.type] ?? "•"}</span>
              {opportunity.title}
            </p>
            <p className="text-muted-foreground text-sm">{opportunity.description}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
