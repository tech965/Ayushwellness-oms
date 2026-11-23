/** A share as a percentage of a denominator, or `null` when it is undefined
 * (zero/absent denominator, absent numerator) -- never 0, NaN or Infinity.
 * Callers render `null` as an em dash.
 */
export function percentOf(
  count: number | null | undefined,
  denominator: number | null | undefined
): number | null {
  if (count === null || count === undefined) return null
  if (!denominator || denominator <= 0 || !Number.isFinite(denominator)) return null
  return (count / denominator) * 100
}

/** One decimal place everywhere: "35.1%". `null` renders as "—". */
export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—"
  return `${value.toFixed(1)}%`
}

/** `count` as a one-decimal percentage of `denominator` ("—" if undefined). */
export function formatShare(
  count: number | null | undefined,
  denominator: number | null | undefined
): string {
  return formatPercent(percentOf(count, denominator))
}
