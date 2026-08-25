import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

export default function AlertsPage() {
  return (
    <>
      <PageHeader title="Alerts" />
      <PhasePlaceholder
        module="Alerts"
        phase="Phase 3"
        description="Operational alerts (delays, NDR, RTO, sync failures) are implemented in Phase 3."
      />
    </>
  )
}
