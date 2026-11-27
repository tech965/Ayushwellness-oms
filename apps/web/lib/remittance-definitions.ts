/** One wording per figure, used for card tooltips and the drilldown header.
 * The Excel exports carry the same meanings (`DEFINITIONS` in
 * `apps/api/app/services/remittance_common.py`).
 *
 * "Settled" is what the courier / gateway REPORTS. "Bank confirmed" is what a
 * matching bank credit proves. They are never added together.
 */
export const COD_DEFINITIONS = {
  expected: "COD amount of every delivered COD order in view.",
  received:
    "Remittance amount reported by the courier partner. Not proof of a bank credit.",
  bank_confirmed:
    "Courier-reported remittance covered by actual bank credits (statement-matched or manually verified). Never more than was credited.",
  pending:
    "COD amount of delivered orders with no courier-reported remittance and no adjustment.",
  short:
    "COD amount − (COD settled, courier-reported + adjustments), where that is short by more than ₹1.",
  excess:
    "(COD settled, courier-reported + adjustments) − COD amount, where that is over by more than ₹1.",
  unmatched:
    "Courier-reported remittance lines not matched to a delivered COD order. Bank credits are shown separately.",
  unmatched_bank_credits:
    "Bank credit amounts not linked to any remittance or settlement reference (COD or prepaid).",
  reconciled:
    "COD settled equals the COD amount and is bank-confirmed, or the order is fully covered by a reasoned adjustment.",
  delivered: "Delivered orders paid by cash on delivery.",
  bank_remaining:
    "Total expected − amount bank confirmed, per order and never below zero.",
  partially_confirmed:
    "Some, but not all, of the expected amount is bank confirmed. The actual credited amount counts; a credit covering several orders is split pro rata.",
  adjusted:
    "Orders with reasoned manual adjustments. Adjustments change the per-order status difference, never bank-confirmed money.",
  total_cod_orders:
    "Every non-cancelled COD order in the period, delivered or not. Wider than every other figure on this page, which covers delivered COD orders only -- so this card has no drilldown here.",
  payment_success:
    "Delivered COD orders that are fully remitted by the courier AND bank-confirmed -- a bank statement credit linked to the remittance, or a person-verified bank credit. Shiprocket's own payout fields are a courier report, never bank confirmation, and an order settled only by a manual adjustment is not a payment.",
  payment_initiated:
    "Delivered COD orders where the courier has reported a remittance that is not yet a clean, bank-confirmed match: a remittance that is partial, short, excess or not yet bank-confirmed, or an order whose Shiprocket record shows a payout reference (UTR) or date but no amount. A courier payout is not a bank settlement.",
  payment_pending:
    'Delivered COD orders with no courier-reported remittance and no Shiprocket payout reference or date. It means "none on record", not "none issued": orders never read from Shiprocket or listed in a CRF report are included (see the data notice above). Can be lower than "Pending Settlement", which also counts orders whose payout reference is known but whose amount is not.',
} as const

export const PREPAID_DEFINITIONS = {
  gross: "Captured payments recorded for these orders. A payment is not a settlement.",
  reported_settlement:
    "Successful net settlement reported by Cashfree and other gateways' uploaded reports for orders in view. Only recognised success statuses count; failed, pending and unverified lines do not. Not proof of a bank credit.",
  bank_settlement:
    "Successful gateway-reported net settlement covered by actual bank credits (statement-matched or manually verified). Never more than was credited.",
  pending:
    "Gross amount captured on orders with no successful gateway-reported settlement and no adjustment.",
  charges: "Gateway charges from Cashfree settlements / uploaded reports.",
  gst: "GST on gateway charges, as reported.",
  refunds: "Completed refunds on these orders.",
  difference:
    "Expected net (gross − charges − GST − refunds) − Total Settlement Received. Includes amounts still pending; bank credits and adjustments do not change it.",
  unmatched:
    "Gateway-reported settlement lines not matched to a prepaid order. Unsuccessful lines are listed but add nothing to the amount. Bank credits are shown separately.",
  unmatched_bank_credits:
    "Bank credit amounts not linked to any remittance or settlement reference (COD or prepaid).",
  reconciled:
    "Settlement received plus manual adjustments equals expected net, and the bank covers the gateway-reported settlement. Adjustments are not bank money.",
  orders: "All prepaid orders in the period.",
  expected_net: "Gross − charges − GST − refunds, for paid orders.",
  bank_remaining:
    "Total expected − amount bank confirmed, per order and never below zero.",
  partially_confirmed:
    "Some, but not all, of the expected amount is bank confirmed. The actual credited amount counts; a credit covering several orders is split pro rata.",
  adjusted:
    "Orders with reasoned manual adjustments. Adjustments change the per-order status difference, never bank-confirmed money.",
  unverified:
    "Orders with a settlement line whose status is blank, unknown or unfamiliar. These lines count toward no settlement total.",
} as const

export function definitionOf(
  module: "cod" | "prepaid",
  drill: string
): string | undefined {
  const table: Record<string, string> =
    module === "cod" ? COD_DEFINITIONS : PREPAID_DEFINITIONS
  return table[drill]
}

/** Must match `SUCCESS_LINE_STATES` in the API. */
export const SUCCESS_STATUSES = [
  "success",
  "successful",
  "settled",
  "processed",
  "completed",
] as const
