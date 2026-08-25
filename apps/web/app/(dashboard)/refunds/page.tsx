import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

export default function RefundsPage() {
  return (
    <>
      <PageHeader title="Refunds" />
      <PhasePlaceholder
        module="Refunds"
        phase="Phase 1"
        description="Refund tracking is implemented in Phase 1."
      />
    </>
  )
}
