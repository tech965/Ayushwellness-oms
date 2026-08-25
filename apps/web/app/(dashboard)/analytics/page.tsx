import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

export default function AnalyticsPage() {
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
