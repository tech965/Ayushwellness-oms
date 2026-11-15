export const fmt = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : new Intl.NumberFormat("en-IN").format(n)

/** Parses a positive whole-number quantity field. */
export function parseQty(v: string): number | null {
  if (!/^\d+$/.test(v.trim())) return null
  const n = Number(v)
  return n > 0 ? n : null
}
