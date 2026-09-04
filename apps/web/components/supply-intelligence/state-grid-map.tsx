"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatMoney } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { MapMetric, StateMetric } from "@/types/supply-intelligence"

/** Approximate grid position of each state/UT, arranged to roughly
 * mirror India's real geography (north at the top, south at the
 * bottom, west/east left-to-right) -- a "grid cartogram," the same
 * pattern BI tools (Tableau/Power BI) use for India/US state maps
 * instead of a literal boundary map. Chosen deliberately over a real
 * SVG/TopoJSON choropleth: no new npm dependency, no unverified
 * geodata whose accuracy/licensing couldn't be checked before this
 * went live, and equal-size cells read better at small sizes than
 * tiny, oddly-shaped real state outlines. Positions are illustrative
 * layout only -- never used for any calculation.
 */
const STATE_GRID: Record<string, { row: number; col: number }> = {
  "Jammu and Kashmir": { row: 0, col: 2 },
  Ladakh: { row: 0, col: 4 },
  "Himachal Pradesh": { row: 1, col: 2 },
  Punjab: { row: 1, col: 1 },
  "Arunachal Pradesh": { row: 1, col: 7 },
  Chandigarh: { row: 2, col: 1 },
  Haryana: { row: 2, col: 2 },
  Uttarakhand: { row: 2, col: 3 },
  Sikkim: { row: 2, col: 6 },
  Nagaland: { row: 2, col: 7 },
  Delhi: { row: 3, col: 2 },
  Rajasthan: { row: 3, col: 1 },
  "Uttar Pradesh": { row: 3, col: 3 },
  Bihar: { row: 3, col: 4 },
  Meghalaya: { row: 3, col: 5 },
  Assam: { row: 3, col: 6 },
  Manipur: { row: 3, col: 7 },
  Gujarat: { row: 4, col: 0 },
  "Madhya Pradesh": { row: 4, col: 2 },
  Chhattisgarh: { row: 4, col: 3 },
  Jharkhand: { row: 4, col: 4 },
  "West Bengal": { row: 4, col: 5 },
  Tripura: { row: 4, col: 6 },
  Mizoram: { row: 4, col: 7 },
  "Dadra and Nagar Haveli and Daman and Diu": { row: 5, col: 1 },
  Maharashtra: { row: 5, col: 2 },
  Telangana: { row: 5, col: 3 },
  Odisha: { row: 5, col: 4 },
  Goa: { row: 6, col: 1 },
  Karnataka: { row: 6, col: 2 },
  "Andhra Pradesh": { row: 6, col: 3 },
  Kerala: { row: 7, col: 2 },
  "Tamil Nadu": { row: 7, col: 3 },
  Puducherry: { row: 7, col: 4 },
  Lakshadweep: { row: 8, col: 1 },
  "Andaman and Nicobar Islands": { row: 8, col: 6 },
}

const STATE_ABBREVIATIONS: Record<string, string> = {
  "Andhra Pradesh": "AP",
  "Arunachal Pradesh": "AR",
  Assam: "AS",
  Bihar: "BR",
  Chhattisgarh: "CG",
  Goa: "GA",
  Gujarat: "GJ",
  Haryana: "HR",
  "Himachal Pradesh": "HP",
  Jharkhand: "JH",
  Karnataka: "KA",
  Kerala: "KL",
  "Madhya Pradesh": "MP",
  Maharashtra: "MH",
  Manipur: "MN",
  Meghalaya: "ML",
  Mizoram: "MZ",
  Nagaland: "NL",
  Odisha: "OD",
  Punjab: "PB",
  Rajasthan: "RJ",
  Sikkim: "SK",
  "Tamil Nadu": "TN",
  Telangana: "TG",
  Tripura: "TR",
  "Uttar Pradesh": "UP",
  Uttarakhand: "UK",
  "West Bengal": "WB",
  "Andaman and Nicobar Islands": "AN",
  Chandigarh: "CH",
  "Dadra and Nagar Haveli and Daman and Diu": "DN",
  Delhi: "DL",
  "Jammu and Kashmir": "JK",
  Ladakh: "LA",
  Lakshadweep: "LD",
  Puducherry: "PY",
}

const GRID_ROWS = 9
const GRID_COLS = 8

function metricValue(state: StateMetric, metric: MapMetric): number {
  if (metric === "orders") return state.orders
  if (metric === "revenue") return Number(state.revenue)
  if (metric === "customers") return state.customers
  return state.rto_rate_pct ?? 0
}

function formatMetricValue(state: StateMetric, metric: MapMetric): string {
  if (metric === "orders") return state.orders.toLocaleString("en-IN")
  if (metric === "revenue") return formatMoney(state.revenue)
  if (metric === "customers") return state.customers.toLocaleString("en-IN")
  return state.rto_rate_pct === null ? "—" : `${state.rto_rate_pct.toFixed(1)}%`
}

interface StateGridMapProps {
  states: StateMetric[]
  metric: MapMetric
  selectedState: string | null
  onSelectState: (state: string) => void
}

export function StateGridMap({
  states,
  metric,
  selectedState,
  onSelectState,
}: StateGridMapProps) {
  const byState = new Map(states.map((s) => [s.state, s]))
  const maxValue = Math.max(1, ...states.map((s) => metricValue(s, metric)))

  return (
    <Card>
      <CardHeader>
        <CardTitle>India Demand Map</CardTitle>
      </CardHeader>
      <CardContent>
        <div
          className="mx-auto grid aspect-[8/9] max-w-xl gap-1"
          style={{
            gridTemplateColumns: `repeat(${GRID_COLS}, minmax(0, 1fr))`,
            gridTemplateRows: `repeat(${GRID_ROWS}, minmax(0, 1fr))`,
          }}
        >
          {Object.entries(STATE_GRID).map(([stateName, pos]) => {
            const state = byState.get(stateName)
            const hasData = Boolean(state && state.orders > 0)
            const intensity = hasData && state ? metricValue(state, metric) / maxValue : 0
            const isSelected = selectedState === stateName

            return (
              <button
                key={stateName}
                type="button"
                onClick={() => onSelectState(stateName)}
                aria-label={stateName}
                style={{
                  gridColumn: pos.col + 1,
                  gridRow: pos.row + 1,
                  backgroundColor: hasData
                    ? `color-mix(in srgb, var(--chart-1) ${Math.round(Math.max(0.2, intensity) * 100)}%, var(--muted))`
                    : undefined,
                }}
                className={cn(
                  "group relative flex items-center justify-center rounded-md border text-[10px] font-semibold transition-transform hover:z-10 hover:scale-110 hover:shadow-md",
                  hasData
                    ? "border-transparent text-white"
                    : "border-dashed border-border bg-muted/40 text-muted-foreground",
                  isSelected && "ring-primary ring-offset-background ring-2 ring-offset-1"
                )}
              >
                {STATE_ABBREVIATIONS[stateName]}
                <div className="bg-popover text-popover-foreground pointer-events-none absolute bottom-full left-1/2 z-20 mb-1.5 hidden w-48 -translate-x-1/2 rounded-md border p-2.5 text-left text-xs shadow-md group-hover:block">
                  <p className="mb-1 font-semibold">{stateName}</p>
                  {hasData && state ? (
                    <div className="text-muted-foreground flex flex-col gap-0.5">
                      <span>Orders: {state.orders.toLocaleString("en-IN")}</span>
                      <span>Revenue: {formatMoney(state.revenue)}</span>
                      <span>Customers: {state.customers.toLocaleString("en-IN")}</span>
                      <span>Delivered: {state.delivered.toLocaleString("en-IN")}</span>
                      <span>RTO: {state.rto.toLocaleString("en-IN")}</span>
                      <span>
                        RTO Rate:{" "}
                        {state.rto_rate_pct === null ? "—" : `${state.rto_rate_pct.toFixed(1)}%`}
                      </span>
                      <span className="text-foreground mt-1 font-medium">
                        {formatMetricValue(state, metric)}
                      </span>
                    </div>
                  ) : (
                    <p className="text-muted-foreground">No order data</p>
                  )}
                </div>
              </button>
            )
          })}
        </div>
        <p className="text-muted-foreground mt-3 text-center text-xs">
          Grid layout approximates India&apos;s geography (not to scale). Click a state to see
          details below.
        </p>
      </CardContent>
    </Card>
  )
}
