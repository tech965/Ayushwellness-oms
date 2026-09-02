/**
 * Builds the `/payments`-filtered drill-down URL the Payments dashboard's
 * KPI cards and method-breakdown chart link to. Pulled out of the page
 * component as a pure function specifically so this exact bug (a
 * hardcoded `provider=cashfree` left over from when these cards were
 * Cashfree-only, which pointed every drill-down at a filter that
 * excluded the Shopify rows the generic analytics now aggregate) has a
 * real, isolated regression test rather than only a manual trace.
 */
export function buildPaymentDrilldownHref(
  currentFilters: { provider?: string; date_from?: string; date_to?: string },
  extra: Record<string, string> = {}
): string {
  const params = new URLSearchParams()
  // Mirrors whatever provider the KPI cards themselves are currently
  // aggregating over -- "All providers" (no filter selected) must drill
  // down to no `provider` param at all, never a value the cards weren't
  // actually showing.
  if (currentFilters.provider) params.set("provider", currentFilters.provider)
  if (currentFilters.date_from) params.set("date_from", currentFilters.date_from)
  if (currentFilters.date_to) params.set("date_to", currentFilters.date_to)
  for (const [key, value] of Object.entries(extra)) params.set(key, value)
  return `/payments?${params.toString()}`
}
