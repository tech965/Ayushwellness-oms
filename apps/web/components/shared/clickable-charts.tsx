"use client"

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  ComposedChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { cn } from "@/lib/utils"

export interface ChartDatum {
  key: string
  label: string
  /** `null` = not available: no bar is drawn (never shown as 0). */
  value: number | null
  color?: string
  /** Optional second measure, shown in the tooltip (or as a line). */
  secondary?: number | null
}

const DEFAULT_COLOR = "#2563eb"
const MUTED = "#cbd5e1"

/** recharts hands a bar click either the original datum or a wrapper with
 * it under `payload`; accept both so selection never silently fails.
 */
export function datumKey(entry: unknown): string | undefined {
  const e = entry as { key?: unknown; payload?: { key?: unknown } } | null
  const key = e?.payload?.key ?? e?.key
  return typeof key === "string" ? key : undefined
}

interface BarsProps {
  data: ChartDatum[]
  selectedKey: string | null
  onSelect: (key: string) => void
  /** Axis/tooltip label for `value`, e.g. "Orders". */
  valueLabel: string
  /** Label for the optional `secondary` series, e.g. "Avg. days". */
  secondaryLabel?: string
  /** Draw `secondary` as a line on a second axis instead of tooltip-only. */
  showSecondaryLine?: boolean
  layout?: "horizontal" | "vertical"
  height?: number
  valueSuffix?: string
  ariaLabel: string
}

/** A bar chart whose bars are clickable (and highlight when selected). */
export function ClickableBarChart({
  data,
  selectedKey,
  onSelect,
  valueLabel,
  secondaryLabel,
  showSecondaryLine = false,
  layout = "horizontal",
  height = 260,
  valueSuffix = "",
  ariaLabel,
}: BarsProps) {
  const vertical = layout === "vertical"
  const hasSecondary = showSecondaryLine && data.some((d) => d.secondary != null)
  const Chart = hasSecondary ? ComposedChart : BarChart
  return (
    <div
      role="img"
      aria-label={ariaLabel}
      className="w-full"
      style={{ height: vertical ? Math.max(height, data.length * 34 + 30) : height }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <Chart
          data={data}
          layout={vertical ? "vertical" : "horizontal"}
          margin={{ top: 8, right: 12, bottom: 4, left: vertical ? 8 : 0 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            vertical={vertical}
            horizontal={!vertical}
          />
          {vertical ? (
            <>
              <XAxis type="number" allowDecimals={false} fontSize={12} />
              <YAxis type="category" dataKey="label" width={150} fontSize={12} />
            </>
          ) : (
            <>
              <XAxis dataKey="label" fontSize={12} interval="preserveStartEnd" />
              <YAxis yAxisId="left" allowDecimals={false} fontSize={12} width={40} />
              {hasSecondary && (
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  fontSize={12}
                  width={40}
                  domain={[0, "auto"]}
                  tickFormatter={(v) => String(Math.round(Number(v) * 10) / 10)}
                />
              )}
            </>
          )}
          <Tooltip
            formatter={(value, name) => [
              `${value}${valueSuffix && name === valueLabel ? valueSuffix : ""}`,
              name,
            ]}
          />
          <Bar
            yAxisId={vertical ? undefined : "left"}
            dataKey="value"
            name={valueLabel}
            radius={vertical ? [0, 4, 4, 0] : [4, 4, 0, 0]}
            cursor="pointer"
            isAnimationActive={false}
            onClick={(entry: unknown) => {
              const key = datumKey(entry)
              if (key) onSelect(key)
            }}
          >
            {data.map((d) => (
              <Cell
                key={d.key}
                fill={
                  selectedKey && selectedKey !== d.key
                    ? MUTED
                    : (d.color ?? DEFAULT_COLOR)
                }
              />
            ))}
          </Bar>
          {hasSecondary && (
            <Line
              yAxisId="right"
              type="monotone"
              dataKey="secondary"
              name={secondaryLabel ?? "Average"}
              stroke="#f97316"
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
              connectNulls
            />
          )}
        </Chart>
      </ResponsiveContainer>
    </div>
  )
}

interface DonutProps {
  data: ChartDatum[]
  selectedKey: string | null
  onSelect: (key: string) => void
  centerLabel: string
  ariaLabel: string
}

/** A donut whose segments AND legend rows are clickable. The legend doubles
 * as the keyboard/screen-reader path to every segment and shows exact counts.
 */
export function ClickableDonut({
  data,
  selectedKey,
  onSelect,
  centerLabel,
  ariaLabel,
}: DonutProps) {
  const visible = data.filter((d) => (d.value ?? 0) > 0)
  const total = visible.reduce((sum, d) => sum + (d.value ?? 0), 0)
  if (total <= 0) {
    return (
      <p className="text-muted-foreground text-sm">No orders in the selected range.</p>
    )
  }
  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row">
      <div role="img" aria-label={ariaLabel} className="relative size-48 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={visible}
              dataKey="value"
              nameKey="label"
              innerRadius="62%"
              outerRadius="100%"
              paddingAngle={visible.length > 1 ? 2 : 0}
              stroke="none"
              cursor="pointer"
              isAnimationActive={false}
              onClick={(entry: unknown) => {
                const key = datumKey(entry)
                if (key) onSelect(key)
              }}
            >
              {visible.map((d) => (
                <Cell
                  key={d.key}
                  fill={
                    selectedKey && selectedKey !== d.key
                      ? MUTED
                      : (d.color ?? DEFAULT_COLOR)
                  }
                />
              ))}
            </Pie>
            <Tooltip formatter={(value, name) => [`${value} orders`, name]} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-semibold tabular-nums">{total}</span>
          <span className="text-muted-foreground text-xs">{centerLabel}</span>
        </div>
      </div>
      <ul className="grid w-full min-w-0 flex-1 gap-1">
        {visible.map((d) => {
          const pct = Math.round(((d.value ?? 0) / total) * 1000) / 10
          return (
            <li key={d.key}>
              <button
                type="button"
                onClick={() => onSelect(d.key)}
                aria-pressed={selectedKey === d.key}
                className={cn(
                  "hover:bg-accent flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-left text-sm",
                  selectedKey === d.key && "bg-accent font-medium"
                )}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    className="size-2.5 shrink-0 rounded-sm"
                    style={{ backgroundColor: d.color ?? DEFAULT_COLOR }}
                  />
                  <span className="truncate">{d.label}</span>
                </span>
                <span className="text-muted-foreground shrink-0 tabular-nums">
                  {d.value} · {pct}%
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export interface GroupedDatum {
  key: string
  label: string
  /** Series values by series key; `null` = not available (no bar drawn). */
  values: Record<string, number | null>
}

interface GroupedProps {
  data: GroupedDatum[]
  series: { key: string; label: string; color: string }[]
  selected: { key: string; series: string } | null
  onSelect: (key: string, series: string) => void
  height?: number
  valueSuffix?: string
  ariaLabel: string
}

/** Side-by-side bars per group (e.g. COD vs Prepaid per shipping partner);
 * clicking a bar reports both the group and the series.
 */
export function ClickableGroupedBarChart({
  data,
  series,
  selected,
  onSelect,
  height = 260,
  valueSuffix = "",
  ariaLabel,
}: GroupedProps) {
  const rows = data.map((d) => ({ key: d.key, label: d.label, ...d.values }))
  return (
    <div role="img" aria-label={ariaLabel} className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" fontSize={12} interval={0} />
          <YAxis fontSize={12} width={44} unit={valueSuffix} />
          <Tooltip formatter={(value, name) => [`${value}${valueSuffix}`, name]} />
          {series.map((s) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label}
              radius={[4, 4, 0, 0]}
              cursor="pointer"
              isAnimationActive={false}
              onClick={(entry: unknown) => {
                const key = datumKey(entry)
                if (key) onSelect(key, s.key)
              }}
            >
              {rows.map((row) => (
                <Cell
                  key={row.key}
                  fill={
                    selected && !(selected.key === row.key && selected.series === s.key)
                      ? MUTED
                      : s.color
                  }
                />
              ))}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
