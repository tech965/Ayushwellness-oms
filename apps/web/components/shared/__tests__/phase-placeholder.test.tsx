import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { PhasePlaceholder } from "@/components/shared/phase-placeholder"

describe("PhasePlaceholder", () => {
  it("renders the module name, phase badge, and description", () => {
    render(
      <PhasePlaceholder
        module="Orders"
        phase="Phase 1"
        description="Order management ships in Phase 1."
      />
    )

    expect(screen.getByText("Orders")).toBeInTheDocument()
    expect(screen.getByText("Phase 1")).toBeInTheDocument()
    expect(screen.getByText("Order management ships in Phase 1.")).toBeInTheDocument()
  })
})
