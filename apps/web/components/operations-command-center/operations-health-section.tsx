import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import type { MetricPair, OperationsHealth } from "@/types/operations-command-center"

function MetricRow({ label, value }: MetricPair) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">
        {value === null ? "—" : value.toLocaleString("en-IN")}
      </span>
    </div>
  )
}

function HealthColumn({ title, metrics }: { title: string; metrics: MetricPair[] }) {
  const hasAnyData = metrics.some((m) => m.value !== null)
  return (
    <div className="flex flex-col gap-2">
      <p className="text-muted-foreground text-xs font-semibold tracking-wide uppercase">
        {title}
      </p>
      {hasAnyData ? (
        metrics.map((metric) => <MetricRow key={metric.label} {...metric} />)
      ) : (
        <p className="text-muted-foreground text-sm">No {title.toLowerCase()} data available for this period.</p>
      )}
    </div>
  )
}

/** Spec section 6/14: a metric that couldn't be computed shows "—", never
 * a fabricated 0 -- see `MetricPair`'s docstring on the backend.
 */
export function OperationsHealthSection({ health }: { health: OperationsHealth }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Operations Health</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <HealthColumn title="Orders" metrics={health.orders} />
          <Separator className="lg:hidden" />
          <HealthColumn title="Payments" metrics={health.payments} />
          <Separator className="lg:hidden" />
          <HealthColumn title="Shipments" metrics={health.shipments} />
          <Separator className="lg:hidden" />
          <HealthColumn title="Returns" metrics={health.returns} />
          <Separator className="lg:hidden" />
          <HealthColumn title="Refunds" metrics={health.refunds} />
        </div>
      </CardContent>
    </Card>
  )
}
