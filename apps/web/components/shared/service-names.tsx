/**
 * Original courier/service names behind a carrier family, e.g. a "Blue Dart"
 * row shows "Blue Dart Surface · Bluedart Surface - Select 500gm · Blue Dart Air".
 * Renders nothing when there is nothing beyond the carrier name itself.
 */
export function ServiceNames({
  names,
  carrier,
}: {
  names?: string[]
  carrier?: string
}) {
  const detail = (names ?? []).filter(
    (n) => n.trim().toLowerCase() !== (carrier ?? "").trim().toLowerCase()
  )
  if (detail.length === 0) return null
  return (
    <span
      className="block text-xs font-normal text-muted-foreground"
      data-testid="service-names"
    >
      {detail.join(" · ")}
    </span>
  )
}
