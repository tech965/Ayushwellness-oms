import { PageHeader } from "@/components/shared/page-header"
import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

export default function RolesPage() {
  return (
    <>
      <PageHeader title="Roles" />
      <PhasePlaceholder
        module="Roles"
        phase="Phase 1"
        description="Role and permission management (RBAC) is implemented in Phase 1."
      />
    </>
  )
}
