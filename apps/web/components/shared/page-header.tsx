import type { ReactNode } from "react"
import Link from "next/link"
import { ArrowLeft } from "lucide-react"

export function PageHeader({
  title,
  description,
  actions,
  backHref,
  backLabel = "Back",
}: {
  title: string
  description?: string
  actions?: ReactNode
  /** Renders a small "← Back" link above the title when set — the shared
   * back-navigation pattern for detail/sub-pages (e.g. a dashboard
   * drill-down page one level below the top-level nav). Omit on
   * top-level pages that don't have a single logical parent.
   */
  backHref?: string
  backLabel?: string
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div>
        {backHref && (
          <Link
            href={backHref}
            className="text-muted-foreground hover:text-foreground mb-1.5 flex w-fit items-center gap-1 text-sm transition-colors"
          >
            <ArrowLeft className="size-3.5" />
            {backLabel}
          </Link>
        )}
        <h1 className="text-foreground text-xl font-semibold tracking-tight">{title}</h1>
        {description && (
          <p className="text-muted-foreground mt-1 text-sm">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
