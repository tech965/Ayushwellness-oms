import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

export default function TasksPage() {
  return (
    <>
      <PageHeader title="Tasks" />
      <PhasePlaceholder
        module="Tasks"
        phase="Phase 3"
        description="Operational tasks generated from NDR/RTO workflows are implemented in Phase 3."
      />
    </>
  )
}
