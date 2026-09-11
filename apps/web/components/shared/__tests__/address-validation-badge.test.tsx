import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"

import { AddressValidationBadge } from "@/components/shared/address-validation-badge"

describe("AddressValidationBadge", () => {
  it("renders a Valid Address result with its score", () => {
    render(<AddressValidationBadge status="valid" score={95} />)
    expect(screen.getByText("95%")).toBeInTheDocument()
    expect(screen.getByText("Valid Address")).toBeInTheDocument()
  })

  it("renders an Ambiguous Address result with its score", () => {
    render(<AddressValidationBadge status="ambiguous" score={50} />)
    expect(screen.getByText("50%")).toBeInTheDocument()
    expect(screen.getByText("Ambiguous Address")).toBeInTheDocument()
  })

  it("renders a Junk Address result with its score", () => {
    render(<AddressValidationBadge status="junk" score={24} />)
    expect(screen.getByText("24%")).toBeInTheDocument()
    expect(screen.getByText("Junk Address")).toBeInTheDocument()
  })

  it("renders 'Validation pending' when there is no stored result yet, never a guessed status", () => {
    render(<AddressValidationBadge status={null} score={null} />)
    expect(screen.getByText("Validation pending")).toBeInTheDocument()
    expect(screen.queryByText(/%$/)).not.toBeInTheDocument()
  })

  it("renders 'No address on file' instead of 'Validation pending' when the order has no shipping address", () => {
    render(<AddressValidationBadge status={undefined} score={undefined} hasAddress={false} />)
    expect(screen.getByText("No address on file")).toBeInTheDocument()
  })

  it("renders a distinct 'Validation Unavailable' label for a provider failure, and never shows a fabricated score", () => {
    render(<AddressValidationBadge status="unknown" score={null} />)
    expect(screen.getByText("Validation Unavailable")).toBeInTheDocument()
    expect(screen.queryByText(/%$/)).not.toBeInTheDocument()
  })
})
