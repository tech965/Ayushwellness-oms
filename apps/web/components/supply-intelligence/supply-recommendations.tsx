import { Sparkles } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { MarketInsight } from "@/types/supply-intelligence"

/** Reuses the exact same `MarketInsight` descriptions Market Intelligence
 * shows -- reframed as recommendation copy, not a second computation.
 * Deliberately labeled "Data-driven insight," never "AI": no model
 * generated this text, it's plain arithmetic over real order/shipment
 * data (see `SupplyIntelligenceService._build_insights`).
 */
export function SupplyRecommendations({ insights }: { insights: MarketInsight[] }) {
  const actionable = insights.filter(
    (insight) => !insight.description.startsWith("Not enough data")
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="text-primary size-4" />
          Supply Intelligence
        </CardTitle>
      </CardHeader>
      <CardContent>
        {actionable.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            Not enough data to generate recommendations for this period.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {actionable.map((insight) => (
              <li key={insight.type} className="flex items-start gap-2 text-sm">
                <Badge variant="secondary" className="mt-0.5 shrink-0">
                  Data-driven insight
                </Badge>
                <span>{insight.description}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
