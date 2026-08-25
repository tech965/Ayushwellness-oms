import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" />
      <PhasePlaceholder
        module="Settings"
        phase="Phase 1"
        description="Account and platform settings are implemented in Phase 1."
      />
    </>
  )
}
