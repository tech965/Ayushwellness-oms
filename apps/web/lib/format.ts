import { format, parseISO } from "date-fns"

export function formatMoney(amount: string | number, currency = "INR"): string {
  const value = typeof amount === "string" ? Number(amount) : amount
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(value)
}

export function formatDate(iso: string): string {
  return format(parseISO(iso), "d MMM yyyy")
}

export function formatDateTime(iso: string): string {
  return format(parseISO(iso), "d MMM yyyy, h:mm a")
}

/** A rate over a zero denominator is undefined, not 0%: show "—". A real
 * 0% (denominator > 0, nothing successful) still renders as "0%".
 */
export function formatRate(rate: number, denominator: number): string {
  return denominator > 0 ? `${rate}%` : "—"
}
