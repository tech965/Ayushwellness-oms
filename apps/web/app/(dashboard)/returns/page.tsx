import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

export default function ReturnsPage() {
  return (
    <>
      <PageHeader title="Returns" />
      <PhasePlaceholder
        module="Returns"
        phase="Phase 1"
        description="Return request tracking is implemented in Phase 1."
      />
    </>
  )
}
