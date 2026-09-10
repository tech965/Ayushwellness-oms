import { describe, expect, it } from "vitest"
import { fireEvent, render } from "@testing-library/react"

import { ProductThumbnail } from "@/components/shared/product-thumbnail"

describe("ProductThumbnail", () => {
  it("renders the image when src is given", () => {
    const { container } = render(<ProductThumbnail src="https://cdn.shopify.com/a.jpg" alt="Widget" />)
    const img = container.querySelector('img[alt="Widget"]')
    expect(img).toHaveAttribute("src", "https://cdn.shopify.com/a.jpg")
  })

  it("renders the placeholder, never a broken <img>, when src is null", () => {
    const { container, getByRole } = render(<ProductThumbnail src={null} alt="Widget" />)
    expect(container.querySelector("img")).toBeNull()
    expect(getByRole("img", { name: "Widget" })).toBeInTheDocument()
  })

  it("falls back to the placeholder when the image fails to load", () => {
    const { container, getByRole } = render(
      <ProductThumbnail src="https://cdn.shopify.com/broken.jpg" alt="Widget" />
    )
    const img = container.querySelector("img") as HTMLImageElement
    fireEvent.error(img)
    expect(container.querySelector("img")).toBeNull()
    expect(getByRole("img", { name: "Widget" })).toBeInTheDocument()
  })

  it("recovers once a NEW src is given after a previous one failed", () => {
    const { container, rerender } = render(
      <ProductThumbnail src="https://cdn.shopify.com/broken.jpg" alt="Widget" />
    )
    fireEvent.error(container.querySelector("img") as HTMLImageElement)
    expect(container.querySelector("img")).toBeNull() // stuck on placeholder

    // Shopify's image changes (e.g. a resync feeds a fresh URL into the
    // same mounted card, via a query invalidation after another edit) --
    // the new URL must get its own fresh chance, not stay hidden behind
    // a stale failure from the old one.
    rerender(<ProductThumbnail src="https://cdn.shopify.com/fixed.jpg" alt="Widget" />)
    const img = container.querySelector('img[alt="Widget"]')
    expect(img).toHaveAttribute("src", "https://cdn.shopify.com/fixed.jpg")
  })
})
