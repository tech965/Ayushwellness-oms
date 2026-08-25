import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

export default function AutomationPage() {
  return (
    <>
      <PageHeader title="Automation" />
      <PhasePlaceholder
        module="Automation"
        phase="Phase 5"
        description="Rule-based automation (WHEN/IF/THEN) for alerts and notifications is implemented in Phase 5."
      />
    </>
  )
}
