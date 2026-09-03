import "@testing-library/jest-dom/vitest"

// jsdom doesn't implement the Pointer Events capture API that Radix UI's
// Select (and other popover-based primitives) call directly on
// pointerdown -- without these no-op shims, any test that opens one of
// those components throws `target.hasPointerCapture is not a function`.
// A well-documented jsdom/Radix gap, not specific to any one component;
// safe, additive, and only affects the test DOM.
if (typeof Element !== "undefined") {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {}
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {}
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {}
  }
}
