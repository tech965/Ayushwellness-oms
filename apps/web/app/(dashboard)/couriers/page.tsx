import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

export default function CouriersPage() {
  return (
    <>
      <PageHeader title="Couriers" />
      <PhasePlaceholder
        module="Couriers"
        phase="Phase 1 / Phase 4"
        description="Courier directory ships in Phase 1; performance scoring and PIN-code/city analytics ship in Phase 4."
      />
    </>
  )
}
