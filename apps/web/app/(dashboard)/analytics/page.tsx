import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"
import { LiveModeGate } from "@/components/shared/live-mode-gate"
import { LiveDashboard } from "@/components/dashboard/live-dashboard"

function StoredDataAnalyticsPage() {
  return (
    <>
      <PageHeader title="Analytics" />
      <PhasePlaceholder
        module="Analytics"
        phase="Phase 3 / Phase 4"
        description="Basic order and delivery analytics ship in Phase 3; courier, city, and PIN-code intelligence ship in Phase 4."
      />
    </>
  )
}

/** While provider data is read live, this page shows its live replacement
 * (or an explanation); otherwise the stored-data screen is unchanged.
 */
export default function Page() {
  return (
    <LiveModeGate live={<LiveDashboard />}>
      <StoredDataAnalyticsPage />
    </LiveModeGate>
  )
}
