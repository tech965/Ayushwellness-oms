import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

export default function UsersPage() {
  return (
    <>
      <PageHeader title="Users" />
      <PhasePlaceholder
        module="Users"
        phase="Phase 1"
        description="User management is implemented in Phase 1 alongside authentication."
      />
    </>
  )
}
