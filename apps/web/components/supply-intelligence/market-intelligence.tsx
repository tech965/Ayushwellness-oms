import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { MarketInsight } from "@/types/supply-intelligence"

const INSIGHT_EMOJI: Record<MarketInsight["type"], string> = {
  strongest_market: "🏆",
  fastest_growing: "📈",
  emerging_market: "🌱",
  attention_required: "⚠️",
  untapped_markets: "🗺️",
}

/** Every card here is text `AnalyticsService`-equivalent generated
 * straight from `SupplyIntelligenceService._build_insights` -- no
 * client-side computation, no invented copy. A card whose backing data
 * was insufficient renders the service's own honest
 * "Not enough data to calculate this insight." string instead of being
 * hidden or faked.
 */
export function MarketIntelligence({ insights }: { insights: MarketInsight[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>📈 Market Intelligence</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {insights.map((insight) => (
          <div key={insight.type} className="rounded-lg border p-4">
            <p className="mb-1 flex items-center gap-1.5 text-sm font-semibold">
              <span>{INSIGHT_EMOJI[insight.type]}</span>
              {insight.title}
            </p>
            <p className="text-muted-foreground text-sm">{insight.description}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
