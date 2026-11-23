import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"

import { ServiceNames } from "@/components/shared/service-names"

describe("ServiceNames", () => {
  it("shows the original service names behind a carrier family", () => {
    render(
      <ServiceNames
        carrier="Blue Dart"
        names={["Blue Dart Surface", "Bluedart Surface - Select 500gm", "Blue Dart Air"]}
      />
    )
    expect(screen.getByTestId("service-names")).toHaveTextContent(
      "Blue Dart Surface · Bluedart Surface - Select 500gm · Blue Dart Air"
    )
  })

  it("renders nothing when the only name is the carrier itself", () => {
    const { container } = render(<ServiceNames carrier="Delhivery" names={["delhivery"]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing when there are no service names", () => {
    const { container } = render(<ServiceNames carrier="DTDC" />)
    expect(container).toBeEmptyDOMElement()
  })
})
