import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { RemittanceDashboard } from "@/components/remittance/remittance-dashboard"
import { renderWithProviders } from "@/test-utils/render-with-providers"
import type { RemittanceSummary } from "@/types/remittance"
import {
  useRemittanceRecords,
  useRemittanceReport,
  useRemittanceSources,
  useCashfreeSyncStatus,
  useRemittanceSummary,
  useRemittanceUnmatched,
  useRemittanceUploads,
  useBankCredits,
  useSettlementLots,
  useRematch,
  useReconciliationDetail,
  downloadDrilldownExport,
} from "@/services/remittance"

const push = vi.fn()
const linkCredit = vi.hoisted(() => vi.fn())
const permissions = new Set<string>()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/reconciliation/cod",
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ hasPermission: (code: string) => permissions.has(code) }),
}))
vi.mock("@/services/remittance", () => ({
  useRemittanceSummary: vi.fn(),
  useRemittanceRecords: vi.fn(),
  useRemittanceUnmatched: vi.fn(),
  useRemittanceReport: vi.fn(),
  useBankCredits: vi.fn(),
  useSettlementLots: vi.fn(),
  useRemittanceUploads: vi.fn(),
  useRemittanceSources: vi.fn(),
  useCashfreeSyncStatus: vi.fn(),
  useCashfreeSyncNow: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRematch: vi.fn(),
  useUploadRemittanceReport: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUploadBankStatement: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAddAdjustment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useLinkBankCredit: () => ({ mutateAsync: linkCredit, isPending: false }),
  downloadRemittanceExport: vi.fn(),
  downloadDrilldownExport: vi.fn(),
  useReconciliationDetail: vi.fn(),
  downloadOriginalFile: vi.fn(),
  downloadUploadTemplate: vi.fn(),
  usePreviewUpload: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useLookupOrderForLine: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useConfirmedMatch: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock("@/components/shared/clickable-charts", () => ({
  ClickableBarChart: ({
    ariaLabel,
    data,
    onSelect,
  }: {
    ariaLabel: string
    data: { key: string; label: string; value: number | null }[]
    onSelect: (key: string) => void
  }) => (
    <div aria-label={ariaLabel}>
      {data.map((d) => (
        <button key={d.key} onClick={() => onSelect(d.key)}>
          {`${ariaLabel} | ${d.label} | ${d.value}`}
        </button>
      ))}
    </div>
  ),
  ClickableDonut: ({
    data,
    onSelect,
  }: {
    data: { key: string; label: string; value: number }[]
    onSelect: (key: string) => void
  }) => (
    <ul>
      {data.map((d) => (
        <li key={d.key}>
          <button
            onClick={() => onSelect(d.key)}
          >{`status | ${d.label} | ${d.value}`}</button>
        </li>
      ))}
    </ul>
  ),
}))

const ok = <T,>(data: T) => ({ data, isLoading: false, isError: false, refetch: vi.fn() })
const META = { page: 1, page_size: 20, total_items: 1, total_pages: 1 }

function pctItem(
  key: string,
  label: string,
  numerator: number,
  denominator: number,
  kind: "count" | "value",
  drill: string
) {
  return {
    key,
    label,
    kind,
    numerator: String(numerator),
    denominator: String(denominator),
    numerator_label: kind === "count" ? "orders" : "rupees",
    denominator_label:
      kind === "count" ? "delivered COD orders" : "expected COD collection",
    value: denominator ? (numerator / denominator) * 100 : null,
    drill,
  }
}

const COD_SUMMARY: RemittanceSummary = {
  kpis: {
    delivered_orders: { count: 6, amount: "6000.00" },
    expected_collection: { count: 6, amount: "6000.00" },
    remittance_received: { count: 5, amount: "4800.00" },
    bank_confirmed: { count: 1, amount: "1000.00" },
    pending: { count: 1, amount: "1000.00" },
    short: { count: 1, amount: "400.00" },
    excess: { count: 1, amount: "200.00" },
    unmatched: { count: 1, amount: "457.00" },
    unmatched_bank_credits: { count: 1, amount: "820.00" },
    reconciled: { count: 1, amount: "1000.00" },
    partially_reconciled: { count: 2, amount: "2000.00" },
  },
  percentages: [
    pctItem("completion", "Reconciliation completion", 1, 6, "count", "reconciled"),
    pctItem(
      "pending_value",
      "Pending remittance (value)",
      1000,
      6000,
      "value",
      "pending"
    ),
    pctItem("unmatched_tx", "Unmatched transactions", 1, 6, "count", "unmatched"),
  ],
  status_breakdown: [
    {
      status: "pending",
      label: "Pending",
      count: 1,
      amount: "1000.00",
      percent_of_orders: 16.7,
    },
    {
      status: "matched",
      label: "Matched",
      count: 1,
      amount: "1000.00",
      percent_of_orders: 16.7,
    },
    {
      status: "short",
      label: "Short",
      count: 1,
      amount: "1000.00",
      percent_of_orders: 16.7,
    },
  ],
  partners: [
    {
      partner: "Delhivery",
      delivered_orders: 6,
      expected_amount: "6000.00",
      remittance_amount: "4800.00",
      amount_received: "1000.00",
      pending_orders: 1,
      short_orders: 1,
      excess_orders: 1,
      matched_orders: 1,
      unmatched_lines: 1,
      completion_percent: 16.7,
    },
  ],
  denominators: { delivered_orders: 6, expected_amount: "6000.00", remittance_lines: 6 },
}

const RECORD = {
  order_id: "o-1",
  order_number: "C3",
  awb: "AWB-C3",
  customer_name: "Asha",
  partner: "Delhivery",
  service_name: "Delhivery Surface",
  order_datetime: "2026-11-10T00:00:00Z",
  delivery_date: "2026-11-12T00:00:00Z",
  expected_amount: "1000.00",
  remittance_amount: "600.00",
  amount_received: "0.00",
  adjustment_amount: "0.00",
  difference: "-400.00",
  remittance_reference: "R2",
  remittance_date: "2026-11-21T00:00:00Z",
  bank_credit_date: null,
  status: "short",
  status_label: "Short",
  source: "upload",
  age_days: 12,
  line_count: 1,
}

function setup(summary: RemittanceSummary = COD_SUMMARY, records: unknown = undefined) {
  vi.mocked(useReconciliationDetail).mockReturnValue(ok(undefined) as never)
  vi.mocked(useRemittanceSummary).mockReturnValue(ok(summary) as never)
  vi.mocked(useRemittanceReport).mockReturnValue(ok(summary.partners ?? []) as never)
  vi.mocked(useRemittanceRecords).mockReturnValue(ok(records) as never)
  vi.mocked(useRemittanceUnmatched).mockReturnValue(ok(undefined) as never)
  vi.mocked(useBankCredits).mockReturnValue(ok(undefined) as never)
  vi.mocked(useSettlementLots).mockReturnValue(ok(undefined) as never)
  vi.mocked(useRemittanceUploads).mockReturnValue(ok([]) as never)
  vi.mocked(useRemittanceSources).mockReturnValue(ok({ cod: [], prepaid: [] }) as never)
  vi.mocked(useCashfreeSyncStatus).mockReturnValue(ok(undefined) as never)
  vi.mocked(useRematch).mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  } as never)
}

const cardLabels = (testId: string) =>
  within(screen.getByTestId(testId))
    .getAllByRole("button")
    .map((button) => button.querySelector("p")?.textContent)

const lastRecordsArgs = () => vi.mocked(useRemittanceRecords).mock.calls.at(-1)![0]
// The reports section also calls the hook (disabled); look for the enabled call.
const unmatchedEnabled = () =>
  vi.mocked(useRemittanceUnmatched).mock.calls.some((call) => call[5] === true)
const card = (label: string) => {
  const found = ["kpi-grid", "kpi-grid-supporting"].flatMap((id) =>
    within(screen.getByTestId(id)).queryAllByRole("button", {
      name: new RegExp(`^${label} `),
    })
  )
  expect(found).toHaveLength(1)
  return found[0]
}

describe("COD remittance dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissions.clear()
    setup()
  })

  it("has no Cashfree sync panel (Cashfree is a prepaid-gateway source only)", () => {
    renderWithProviders(<RemittanceDashboard module="cod" />)
    expect(screen.queryByTestId("cashfree-sync-panel")).not.toBeInTheDocument()
  })

  it("shows exactly the seven required COD cards, plus the supporting figures", () => {
    renderWithProviders(<RemittanceDashboard module="cod" />)
    expect(cardLabels("kpi-grid")).toEqual([
      "Total COD to be Settled",
      "Total COD Settled",
      "Pending Settlement",
      "Short Settlement",
      "Excess Settlement",
      "Unmatched Remittances",
      "Reconciled Orders",
    ])
    expect(cardLabels("kpi-grid-supporting")).toEqual([
      "Total COD Delivered Orders",
      "COD Bank Confirmed",
      "Unmatched Bank Credits",
      "Reconciliation Completion",
    ])
    expect(card("Total COD to be Settled")).toHaveTextContent("₹6,000.00")
    expect(card("Total COD Settled")).toHaveTextContent("₹4,800.00")
    expect(card("Total COD Settled")).toHaveTextContent("5 orders")
    expect(card("Pending Settlement")).toHaveTextContent("₹1,000.00")
    // Courier-reported money and bank money are separate cards, never one sum.
    expect(card("Unmatched Remittances")).toHaveTextContent("₹457.00 courier-reported")
    expect(card("Unmatched Remittances")).not.toHaveTextContent("1,277")
    expect(card("Unmatched Bank Credits")).toHaveTextContent("₹820.00")
    expect(card("Total COD Settled")).toHaveTextContent("COD bank confirmed ₹1,000.00")
    expect(card("COD Bank Confirmed")).toHaveTextContent("₹1,000.00")
    expect(card("Total COD Settled")).toHaveAttribute(
      "title",
      expect.stringContaining("Not proof of a bank credit")
    )
  })

  it("shows one-decimal percentages with the denominator and a count/value tag", () => {
    renderWithProviders(<RemittanceDashboard module="cod" />)
    const completion = screen.getByRole("button", { name: /^Reconciliation completion/ })
    expect(completion).toHaveTextContent("16.7%")
    expect(completion).toHaveTextContent("1 orders of 6 delivered COD orders")
    expect(completion).toHaveTextContent("by count")
    const pendingValue = screen.getByRole("button", {
      name: /Pending remittance \(value\)/,
    })
    expect(pendingValue).toHaveTextContent("16.7%")
    expect(pendingValue).toHaveTextContent(
      "₹1,000.00 rupees of ₹6,000.00 expected COD collection"
    )
    expect(pendingValue).toHaveTextContent("by value")
    expect(card("Pending Settlement")).toHaveTextContent("16.7% of delivered")
  })

  it("shows a dash and says so when a denominator is zero", () => {
    const empty: RemittanceSummary = {
      ...COD_SUMMARY,
      percentages: [
        pctItem("completion", "Reconciliation completion", 0, 0, "count", "reconciled"),
      ],
      kpis: { ...COD_SUMMARY.kpis, delivered_orders: { count: 0, amount: "0.00" } },
      denominators: { delivered_orders: 0, expected_amount: "0.00", remittance_lines: 0 },
    }
    setup(empty)
    renderWithProviders(<RemittanceDashboard module="cod" />)
    const completion = screen.getByRole("button", { name: /^Reconciliation completion/ })
    expect(completion).toHaveTextContent("—")
    expect(completion).toHaveTextContent("Not available")
    expect(document.body.textContent).not.toMatch(/NaN|Infinity/)
  })

  it.each([
    ["Total COD Delivered Orders", "delivered"],
    ["Total COD to be Settled", "expected"],
    ["Total COD Settled", "received"],
    ["COD Bank Confirmed", "bank_confirmed"],
    ["Pending Settlement", "pending"],
    ["Short Settlement", "short"],
    ["Excess Settlement", "excess"],
    ["Reconciled Orders", "reconciled"],
    ["Reconciliation Completion", "reconciled"],
  ])("clicking %s lists the %s records", async (label, kpi) => {
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    expect(lastRecordsArgs().enabled).toBe(false)
    await user.click(card(label))
    expect(lastRecordsArgs()).toMatchObject({ module: "cod", kpi, enabled: true })
    expect(screen.getByTestId("record-drilldown")).toBeInTheDocument()
    expect(card(label)).toHaveAttribute("aria-pressed", "true")
  })

  it("Unmatched Transactions opens the unmatched list, not the order list", async () => {
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(card("Unmatched Remittances"))
    expect(unmatchedEnabled()).toBe(true)
    expect(lastRecordsArgs().enabled).toBe(false)
  })

  it("clicking a percentage opens the numerator records", async () => {
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(screen.getByRole("button", { name: /Pending remittance \(value\)/ }))
    expect(lastRecordsArgs()).toMatchObject({ kpi: "pending", enabled: true })
    await user.click(screen.getByRole("button", { name: /Unmatched transactions/ }))
    expect(unmatchedEnabled()).toBe(true)
  })

  it("status chart and partner chart elements filter the list", async () => {
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(screen.getByRole("button", { name: "status | Short | 1" }))
    expect(lastRecordsArgs().filters.status).toBe("short")
    expect(lastRecordsArgs().kpi).toBeUndefined()
    await user.click(
      screen.getByRole("button", { name: "Delivered orders by partner | Delhivery | 6" })
    )
    expect(lastRecordsArgs().filters.partner).toBe("Delhivery")
  })

  it("lists every field of the COD reconciliation report and opens the order on click", async () => {
    const user = userEvent.setup()
    setup(COD_SUMMARY, { data: [RECORD], meta: META })
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(card("Short Settlement"))
    const list = screen.getByTestId("record-drilldown")
    for (const header of [
      "Order",
      "AWB",
      "Customer",
      "Shipping partner",
      "Delivered",
      "COD amount",
      "COD settled (courier)",
      "COD bank confirmed",
      "Difference",
      "Remittance ref / date",
      "Bank credit date",
      "Status",
    ]) {
      expect(within(list).getByText(header)).toBeInTheDocument()
    }
    expect(within(list).getByText("AWB-C3")).toBeInTheDocument()
    expect(within(list).getByText("-₹400.00")).toBeInTheDocument()
    expect(within(list).getByText("R2")).toBeInTheDocument()
    expect(within(list).getByText("Short")).toBeInTheDocument()
    await user.click(within(list).getByText("C3"))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByRole("link", { name: "Open full order" })).toHaveAttribute(
      "href",
      "/orders/o-1"
    )
    expect(useReconciliationDetail).toHaveBeenCalledWith("cod", "o-1")
  })

  it("passes the page filters to the summary and to the drilldown alike", async () => {
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(card("Pending Settlement"))
    const summaryFilters = vi.mocked(useRemittanceSummary).mock.calls.at(-1)![1]
    const drill = lastRecordsArgs().filters
    for (const key of ["date_from", "date_to", "partner", "status"] as const) {
      expect(drill[key]).toEqual(summaryFilters[key])
    }
  })

  it("Clear selection closes the list", async () => {
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(card("Short Settlement"))
    await user.click(screen.getByRole("button", { name: /Clear selection/ }))
    expect(screen.queryByTestId("record-drilldown")).not.toBeInTheDocument()
    expect(lastRecordsArgs().enabled).toBe(false)
  })

  it("search is passed to the server and paging resets", async () => {
    const user = userEvent.setup()
    setup(COD_SUMMARY, { data: [RECORD], meta: { ...META, total_pages: 2 } })
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(card("Short Settlement"))
    await user.click(screen.getByRole("button", { name: /Next/ }))
    expect(lastRecordsArgs().page).toBe(2)
    await user.type(screen.getByLabelText("Search records"), "AWB-C3")
    await vi.waitFor(() => expect(lastRecordsArgs().filters.q).toBe("AWB-C3"))
    expect(lastRecordsArgs().page).toBe(1)
  })

  it("hides upload / adjust / re-match actions without the permission", () => {
    renderWithProviders(<RemittanceDashboard module="cod" />)
    expect(
      screen.queryByRole("button", { name: /Upload remittance report/ })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /Upload bank statement/ })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /Re-run matching/ })
    ).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Export to Excel/ })).toBeInTheDocument()
  })

  it("shows the actions to a user who may upload and adjust", async () => {
    permissions.add("reconciliation.upload")
    permissions.add("reconciliation.adjust")
    const user = userEvent.setup()
    setup(COD_SUMMARY, { data: [RECORD], meta: META })
    renderWithProviders(<RemittanceDashboard module="cod" />)
    expect(
      screen.getByRole("button", { name: /Upload remittance report/ })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /Upload bank statement/ })
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /Re-run matching/ })).toBeInTheDocument()
    await user.click(card("Short Settlement"))
    expect(screen.getByRole("button", { name: "Adjust" })).toBeInTheDocument()
  })

  it("shows the reconciliation sub-navigation without the Shiprocket Wallet", () => {
    renderWithProviders(<RemittanceDashboard module="cod" />)
    const nav = screen.getByRole("navigation", { name: "Reconciliation sections" })
    expect(within(nav).getByText("COD Remittance Reconciliation")).toBeInTheDocument()
    expect(within(nav).getByText("Prepaid Remittance Reconciliation")).toBeInTheDocument()
    expect(within(nav).queryByText(/Wallet/)).not.toBeInTheDocument()
  })

  it("partner-wise report counts open that partner's records", async () => {
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(screen.getByRole("button", { name: "Delhivery pending orders" }))
    expect(lastRecordsArgs()).toMatchObject({ kpi: "pending" })
    expect(lastRecordsArgs().filters.partner).toBe("Delhivery")
  })

  it("shows an error state with retry when the summary fails", () => {
    vi.mocked(useRemittanceSummary).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("boom"),
      refetch: vi.fn(),
    } as never)
    renderWithProviders(<RemittanceDashboard module="cod" />)
    expect(screen.getByRole("button", { name: /retry|try again/i })).toBeInTheDocument()
  })
})

const PREPAID_SUMMARY: RemittanceSummary = {
  kpis: {
    total_prepaid_orders: { count: 9, amount: "9000.00" },
    gross_collection: { count: 7, amount: "7000.00" },
    gateway_charges: { count: 4, amount: "80.00" },
    gst: { count: 4, amount: "14.40" },
    refunds: { count: 2, amount: "1400.00" },
    expected_net: { count: 7, amount: "5505.60" },
    bank_settlement: { count: 1, amount: "976.40" },
    reported_settlement: { count: 5, amount: "4529.20" },
    settlement_difference: { count: 3, amount: "976.40" },
    unmatched: { count: 1, amount: "300.00" },
    unmatched_bank_credits: { count: 1, amount: "555.00" },
    reconciled: { count: 1, amount: "976.40" },
    pending: { count: 1, amount: "1000.00" },
  },
  percentages: [
    {
      key: "completion",
      label: "Reconciliation completion",
      kind: "count",
      numerator: "1",
      denominator: "7",
      numerator_label: "fully reconciled orders",
      denominator_label: "prepaid orders with a captured payment",
      value: (1 / 7) * 100,
      drill: "reconciled",
    },
  ],
  status_breakdown: [
    {
      status: "failed",
      label: "Failed",
      count: 1,
      amount: "0.00",
      percent_of_orders: 11.1,
    },
  ],
  gateways: [
    {
      gateway: "Cashfree",
      orders: 9,
      gross_amount: "7000.00",
      gateway_charges: "80.00",
      gst_amount: "14.40",
      refund_amount: "1400.00",
      expected_net: "5505.60",
      reported_settlement: "4529.20",
      bank_settlement: "976.40",
      pending_orders: 1,
      matched_orders: 1,
      unmatched_lines: 1,
      completion_percent: 14.3,
    },
  ],
  denominators: {
    prepaid_orders: 9,
    paid_orders: 7,
    gross_collection: "7000.00",
    expected_net: "5505.60",
    settlement_lines: 6,
  },
}

describe("Prepaid remittance dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissions.clear()
    setup(PREPAID_SUMMARY)
    vi.mocked(useRemittanceReport).mockReturnValue(ok(PREPAID_SUMMARY.gateways) as never)
  })

  it("is Cashfree API-first: shows the sync panel, with file upload only as a fallback", () => {
    permissions.add("reconciliation.upload")
    vi.mocked(useCashfreeSyncStatus).mockReturnValue(
      ok({
        connection_status: "not_configured",
        connection_message: "Cashfree credentials are not configured on the server.",
        configured: false,
        environment: null,
        sync_enabled: true,
        sync_interval_minutes: 60,
        last_attempt_at: null,
        last_successful_sync_at: null,
        last_sync_error: null,
        last_webhook_at: null,
        running: false,
        settlement_lots: 0,
        settlement_lots_by_status: {},
        synced_lines: 0,
        bank_confirmation_note: "Bank reconciliation is separate.",
      }) as never
    )
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    expect(screen.getByTestId("cashfree-sync-panel")).toBeInTheDocument()
    expect(screen.getByTestId("cashfree-connection")).toHaveTextContent("Not Configured")
    expect(
      screen.getByRole("button", { name: "Upload settlement report (fallback)" })
    ).toBeInTheDocument()
  })

  it("shows exactly the nine required prepaid cards, plus the supporting figures", () => {
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    expect(cardLabels("kpi-grid")).toEqual([
      "Total Prepaid Collected",
      "Total Settlement Received",
      "Settlement Pending",
      "Gateway Charges",
      "GST",
      "Refunds",
      "Settlement Difference",
      "Unmatched Transactions",
      "Reconciled Transactions",
    ])
    expect(cardLabels("kpi-grid-supporting")).toEqual([
      "Prepaid Bank Confirmed",
      "Unmatched Bank Credits",
      "Total Prepaid Orders",
      "Expected Net Settlement",
      "Reconciliation Completion",
    ])
    expect(card("Total Prepaid Collected")).toHaveTextContent("₹7,000.00")
    expect(card("Gateway Charges")).toHaveTextContent("1.1% of gross")
    expect(card("Refunds")).toHaveTextContent("20.0% of gross")
    // Headline = gateway-reported; the bank figure is shown separately.
    expect(
      card("Total Settlement Received").querySelector("p.text-3xl")
    ).toHaveTextContent("₹4,529.20")
    expect(card("Total Settlement Received")).toHaveTextContent(
      "Prepaid bank confirmed ₹976.40"
    )
    expect(card("Prepaid Bank Confirmed").querySelector("p.text-3xl")).toHaveTextContent(
      "₹976.40"
    )
    expect(card("Settlement Difference")).toHaveTextContent(
      "Expected net − Total Settlement Received"
    )
    expect(card("Unmatched Transactions")).toHaveTextContent("₹300.00 gateway-reported")
    expect(card("Unmatched Bank Credits")).toHaveTextContent("₹555.00")
  })

  it.each([
    ["Total Prepaid Orders", "orders"],
    ["Total Prepaid Collected", "gross"],
    ["Gateway Charges", "charges"],
    ["GST", "gst"],
    ["Refunds", "refunds"],
    ["Expected Net Settlement", "expected_net"],
    ["Total Settlement Received", "reported_settlement"],
    ["Prepaid Bank Confirmed", "bank_settlement"],
    ["Settlement Difference", "difference"],
    ["Settlement Pending", "pending"],
    ["Reconciled Transactions", "reconciled"],
    ["Reconciliation Completion", "reconciled"],
  ])("clicking %s lists the %s records", async (label, kpi) => {
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    await user.click(card(label))
    expect(lastRecordsArgs()).toMatchObject({ module: "prepaid", kpi, enabled: true })
  })

  it("uses gateway wording and the gateway filter key", async () => {
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    await user.click(
      screen.getByRole("button", { name: "Orders by gateway | Cashfree | 9" })
    )
    expect(lastRecordsArgs().filters.gateway).toBe("Cashfree")
    expect(lastRecordsArgs().filters.partner).toBeUndefined()
    expect(
      screen.getByRole("heading", { name: "Prepaid Remittance Reconciliation" })
    ).toBeInTheDocument()
  })

  it("failed status segment lists failed transactions", async () => {
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    await user.click(screen.getByRole("button", { name: "status | Failed | 1" }))
    expect(lastRecordsArgs().filters.status).toBe("failed")
  })

  it("keeps the prepaid report fields in the order list", async () => {
    const user = userEvent.setup()
    setup(PREPAID_SUMMARY, {
      data: [
        {
          order_id: "o-p1",
          order_number: "P1",
          customer_name: "Ravi",
          gateway: "Cashfree",
          gateway_transaction_id: "T1",
          transaction_date: "2026-11-20T00:00:00Z",
          order_datetime: "2026-11-20T00:00:00Z",
          order_amount: "1000.00",
          gross_amount: "1000.00",
          gateway_charges: "20.00",
          gst_amount: "3.60",
          refund_amount: "0.00",
          expected_net: "976.40",
          actual_settlement: "976.40",
          bank_confirmed_settlement: "976.40",
          adjustment_amount: "0.00",
          bank_reference: "U1",
          settlement_date: "2026-11-22T00:00:00Z",
          bank_credit_date: "2026-11-24T00:00:00Z",
          difference: "0.00",
          settlement_gap: "0.00",
          status: "matched",
          status_label: "Matched",
          payment_recorded: true,
          line_count: 1,
          age_days: 4,
        },
      ],
      meta: META,
    })
    vi.mocked(useRemittanceReport).mockReturnValue(ok(PREPAID_SUMMARY.gateways) as never)
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    await user.click(card("Reconciled Transactions"))
    const list = screen.getByTestId("record-drilldown")
    for (const header of [
      "Payment / fulfilment",
      "Gateway / transaction ID",
      "Cashfree payment ID",
      "Settlement ID",
      "Transaction date",
      "Gross",
      "Charges",
      "GST",
      "Refund",
      "Expected net",
      "Settlement received (gateway)",
      "Prepaid bank confirmed",
      "Status difference",
      "UTR / reference",
      "Status",
    ]) {
      expect(within(list).getByText(header)).toBeInTheDocument()
    }
    expect(within(list).getByText("T1")).toBeInTheDocument()
    expect(within(list).getAllByText("₹976.40").length).toBeGreaterThanOrEqual(1)
    expect(within(list).getByText("Matched")).toBeInTheDocument()
  })
})

const UNMATCHED = {
  data: [
    {
      kind: "remittance_line",
      id: "line-1",
      partner: "Delhivery",
      awb: "AWB-STRAY",
      order_ref: null,
      reference: "RM-STRAY",
      amount: "450.00",
      date: "2026-11-01T00:00:00Z",
      reason: "No order found for this AWB / order reference",
    },
    {
      kind: "bank_credit",
      id: "credit-1",
      partner: null,
      awb: null,
      order_ref: null,
      reference: "MISC-1",
      amount: "820.00",
      date: "2026-11-01T00:00:00Z",
      reason: "Bank credit not linked to any remittance or settlement",
    },
  ],
  meta: { page: 1, page_size: 20, total_items: 2, total_pages: 1 },
}

describe("manual COD matching entry point", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissions.clear()
    setup()
    vi.mocked(useRemittanceUnmatched).mockReturnValue(ok(UNMATCHED) as never)
  })

  it("offers 'Match to order' on COD remittance lines (not bank credits) to users who may adjust", async () => {
    permissions.add("reconciliation.adjust")
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(card("Unmatched Remittances"))
    const list = screen.getByTestId("record-drilldown")
    const rows = within(list).getAllByRole("row").slice(1)
    expect(
      within(rows[0]).getByRole("button", { name: "Match to order" })
    ).toBeInTheDocument()
    expect(
      within(rows[1]).queryByRole("button", { name: "Match to order" })
    ).not.toBeInTheDocument()
  })

  it("opens the match dialog for that line with its amount", async () => {
    permissions.add("reconciliation.adjust")
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(card("Unmatched Remittances"))
    await user.click(screen.getByRole("button", { name: "Match to order" }))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByTestId("match-line")).toHaveTextContent("AWB-STRAY")
    expect(within(dialog).getByTestId("match-line")).toHaveTextContent("₹450.00")
    expect(
      within(dialog).getByRole("button", { name: "Match remittance" })
    ).toBeDisabled()
  })

  it("is not offered to a read-only user, or with only the upload permission", async () => {
    for (const perms of [[], ["reconciliation.upload"]]) {
      permissions.clear()
      perms.forEach((p) => permissions.add(p))
      const user = userEvent.setup()
      const { unmount } = renderWithProviders(<RemittanceDashboard module="cod" />)
      await user.click(card("Unmatched Remittances"))
      expect(
        screen.queryByRole("button", { name: "Match to order" })
      ).not.toBeInTheDocument()
      unmount()
    }
  })

  it("is not offered on the prepaid dashboard", async () => {
    permissions.add("reconciliation.adjust")
    setup(PREPAID_SUMMARY)
    vi.mocked(useRemittanceUnmatched).mockReturnValue(ok(UNMATCHED) as never)
    vi.mocked(useRemittanceReport).mockReturnValue(ok(PREPAID_SUMMARY.gateways) as never)
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    await user.click(card("Unmatched Transactions"))
    expect(
      screen.queryByRole("button", { name: "Match to order" })
    ).not.toBeInTheDocument()
  })
})

const totals = (amount: string, count: number, over: Record<string, unknown> = {}) => ({
  measure: "Short amount",
  amount,
  count,
  base_amount: amount,
  base_count: count,
  breakdown: [],
  ...over,
})

describe("drilldown totals, filters, export and detail", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissions.clear()
    setup()
  })

  const openShort = async (data: unknown) => {
    setup(COD_SUMMARY, data)
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(card("Short Settlement"))
    return user
  }

  it("shows the exact total and says it matches the card", async () => {
    await openShort({
      data: [{ ...RECORD, contribution: "400.00" }],
      meta: META,
      totals: totals("400.00", 1),
    })
    const box = screen.getByTestId("drilldown-totals")
    expect(within(box).getByTestId("drilldown-total")).toHaveTextContent("₹400.00")
    expect(box).toHaveTextContent("Matches the card: ₹400.00 · 1 record")
    expect(box).toHaveTextContent("This page adds up to ₹400.00")
    expect(screen.getByText("In total")).toBeInTheDocument()
  })

  it("warns instead of claiming a match when the totals differ from the card", async () => {
    await openShort({ data: [], meta: META, totals: totals("399.99", 1) })
    const box = screen.getByTestId("drilldown-totals")
    expect(box).not.toHaveTextContent("Matches the card")
    expect(within(box).getByRole("alert")).toHaveTextContent("The card shows ₹400.00")
  })

  it("shows an empty state with a zero total", async () => {
    await openShort({
      data: [],
      meta: { ...META, total_items: 0, total_pages: 0 },
      totals: totals("0.00", 0),
    })
    expect(screen.getByTestId("drilldown-total")).toHaveTextContent("₹0.00")
    expect(screen.getByText("No records match this selection")).toBeInTheDocument()
  })

  it("has date and status filters and exports exactly the open selection", async () => {
    const user = await openShort({ data: [], meta: META, totals: totals("400.00", 1) })
    const filters = screen.getByTestId("drilldown-filters")
    expect(
      within(filters).getByRole("combobox", { name: "Drilldown status" })
    ).toBeInTheDocument()
    await user.type(screen.getByLabelText("Search records"), "AWB-C3")
    await vi.waitFor(() => expect(lastRecordsArgs().filters.q).toBe("AWB-C3"))
    await user.click(
      within(filters).getByRole("button", { name: "Export these records" })
    )
    const [module, params] = vi.mocked(downloadDrilldownExport).mock.calls.at(-1)!
    expect(module).toBe("cod")
    expect(params).toMatchObject({ view: "records", kpi: "short" })
    expect(params.filters).toEqual(lastRecordsArgs().filters)
  })

  it("the unmatched drilldown has no status filter, shows its breakdown and opens a line", async () => {
    vi.mocked(useRemittanceUnmatched).mockReturnValue(
      ok({
        ...UNMATCHED,
        totals: totals("1270.00", 2, {
          measure: "Unmatched amount",
          breakdown: [
            { label: "Remittance lines", count: 1, amount: "450.00" },
            { label: "Bank credits", count: 1, amount: "820.00" },
          ],
        }),
      }) as never
    )
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(card("Unmatched Remittances"))
    const filters = screen.getByTestId("drilldown-filters")
    expect(within(filters).queryByRole("combobox")).not.toBeInTheDocument()
    expect(screen.getByTestId("drilldown-totals")).toHaveTextContent(
      "Remittance lines: ₹450.00 (1) · Bank credits: ₹820.00 (1)"
    )
    await user.click(
      within(filters).getByRole("button", { name: "Export these records" })
    )
    expect(vi.mocked(downloadDrilldownExport).mock.calls.at(-1)![1]).toMatchObject({
      view: "unmatched",
    })
    await user.click(
      within(screen.getByTestId("record-drilldown")).getByText("AWB-STRAY")
    )
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent("Unmatched remittance line")
    expect(dialog).toHaveTextContent("RM-STRAY")
    expect(dialog).toHaveTextContent("₹450.00")
  })

  it("gives a read-only user drilldown, export and detail but no write action", async () => {
    const user = await openShort({
      data: [{ ...RECORD, contribution: "400.00" }],
      meta: META,
      totals: totals("400.00", 1),
    })
    expect(screen.queryByRole("button", { name: "Adjust" })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Export these records" })).toBeEnabled()
    await user.click(within(screen.getByTestId("record-drilldown")).getByText("C3"))
    const dialog = await screen.findByRole("dialog")
    expect(
      within(dialog).queryByRole("button", { name: /adjust|match|upload|link bank/i })
    ).not.toBeInTheDocument()
  })

  it("renders the full reconciliation detail with its evidence", async () => {
    const user = await openShort({ data: [RECORD], meta: META })
    vi.mocked(useReconciliationDetail).mockReturnValue(
      ok({
        module: "cod",
        record: { ...RECORD, payment_status: "pending", delivery_status: "delivered" },
        lines: [
          {
            id: "l1",
            source: "upload",
            partner: "Delhivery",
            awb: "AWB-C3",
            order_ref: null,
            reference: "R2",
            amount: "600.00",
            date: "2026-11-21T00:00:00Z",
            match_method: "awb",
          },
        ],
        bank_links: [],
        adjustments: [],
      }) as never
    )
    await user.click(within(screen.getByTestId("record-drilldown")).getByText("C3"))
    const dialog = await screen.findByRole("dialog")
    for (const text of [
      "Courier partner",
      "COD amount",
      "Remittance amount",
      "Settlement date",
      "Difference",
      "Remittance lines",
      "No bank credit is linked yet",
    ]) {
      expect(dialog).toHaveTextContent(text)
    }
  })
})

describe("reported and bank-confirmed stay separate", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissions.clear()
  })

  const lastUnmatchedKinds = () =>
    vi
      .mocked(useRemittanceUnmatched)
      .mock.calls.filter((call) => call[5] === true)
      .map((call) => call[2])

  it.each([
    ["cod", "Unmatched Remittances", "remittance_line"],
    ["cod", "Unmatched Bank Credits", "bank_credit"],
    ["prepaid", "Unmatched Transactions", "gateway_line"],
    ["prepaid", "Unmatched Bank Credits", "bank_credit"],
  ] as const)("%s: %s lists only %s rows", async (module, label, kind) => {
    setup(module === "cod" ? COD_SUMMARY : PREPAID_SUMMARY)
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module={module} />)
    await user.click(card(label))
    expect(lastUnmatchedKinds().at(-1)).toBe(kind)
    expect(card(label)).toHaveAttribute("aria-pressed", "true")
    const other = label === "Unmatched Bank Credits" ? /^Unmatched (Rem|Trans)/ : null
    if (other) {
      expect(screen.getByRole("button", { name: other })).toHaveAttribute(
        "aria-pressed",
        "false"
      )
    }
  })

  it.each([
    ["Total Settlement Received", "4529.20", "5", "Not proof of a bank credit"],
    ["Prepaid Bank Confirmed", "976.40", "1", "covered by actual bank credits"],
    ["Settlement Difference", "976.40", "3", "− Total Settlement Received"],
  ])(
    "%s drilldown shows its definition and checks its own card",
    async (label, amount, count, meaning) => {
      setup(PREPAID_SUMMARY, {
        data: [],
        meta: META,
        totals: totals(amount, Number(count)),
      })
      const user = userEvent.setup()
      renderWithProviders(<RemittanceDashboard module="prepaid" />)
      await user.click(card(label))
      expect(screen.getByTestId("drilldown-definition")).toHaveTextContent(meaning)
      expect(screen.getByTestId("drilldown-totals")).toHaveTextContent("Matches the card")
    }
  )
})

const POSITION_KPIS = {
  bank_position: {
    count: 9,
    amount: "976.40",
    expected: "5505.60",
    statement_matched: "576.40",
    manually_verified: "400.00",
    remaining: "4529.20",
    over_confirmed: "0.00",
  },
  bank_remaining: { count: 6, amount: "4529.20" },
  partially_confirmed: {
    count: 2,
    amount: "576.40",
    expected: "1552.80",
    confirmed: "976.40",
  },
  adjusted: { count: 1, amount: "76.40" },
  settlement_difference: {
    count: 3,
    amount: "976.40",
    adjustments: "76.40",
    after_adjustments: "900.00",
  },
  unverified: {
    count: 2,
    amount: "2000.00",
    unmatched_lines: 1,
    unmatched_amount: "50.00",
  },
}

describe("bank confirmation, adjustments and unverified statuses", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissions.clear()
  })

  const withPosition: RemittanceSummary = {
    ...PREPAID_SUMMARY,
    kpis: { ...PREPAID_SUMMARY.kpis, ...POSITION_KPIS },
  }

  it("shows expected, confirmed, remaining and Partially Confirmed", () => {
    setup(withPosition)
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    const panel = screen.getByTestId("bank-position")
    for (const text of [
      "Total expected",
      "₹5,505.60",
      "Amount bank confirmed",
      "₹976.40",
      "Remaining amount",
      "₹4,529.20",
      "Partially Confirmed",
      "2 orders",
      "Statement-matched ₹576.40 · Manually verified ₹400.00",
    ]) {
      expect(panel).toHaveTextContent(text)
    }
  })

  it("keeps the headline difference and shows adjustments beside it", () => {
    setup(withPosition)
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    expect(card("Settlement Difference").querySelector("p.text-3xl")).toHaveTextContent(
      "₹976.40"
    )
    const panel = screen.getByTestId("adjustment-position")
    expect(panel).toHaveTextContent("Settlement Difference (headline)₹976.40")
    expect(panel).toHaveTextContent("+₹76.40")
    expect(panel).toHaveTextContent("Difference less adjustments₹900.00")
    expect(panel).toHaveTextContent("The headline never includes adjustments")
  })

  it("adds no KPI cards: the grids are unchanged", () => {
    setup(withPosition)
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    expect(cardLabels("kpi-grid")).toHaveLength(9)
    expect(cardLabels("kpi-grid-supporting")).toHaveLength(5)
  })

  it.each([
    [/^Total expected/, "expected_net"],
    [/^Amount bank confirmed/, "bank_settlement"],
    [/^Remaining amount/, "bank_remaining"],
    [/^Partially Confirmed/, "partially_confirmed"],
    [/^Manual adjustments/, "adjusted"],
    [/^Unverified lines/, "unverified"],
  ])("the %s figure opens the %s records", async (name, kpi) => {
    setup(withPosition)
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    await user.click(screen.getByRole("button", { name }))
    expect(lastRecordsArgs()).toMatchObject({ kpi, enabled: true })
    expect(screen.getByRole("button", { name })).toHaveAttribute("aria-pressed", "true")
  })

  it("warns about unverified statuses only when there are some", () => {
    setup(withPosition)
    const { unmount } = renderWithProviders(<RemittanceDashboard module="prepaid" />)
    expect(screen.getByTestId("unverified-notice")).toHaveTextContent(
      "counted in no total: ₹2,000.00 on 2 orders, and 1 unmatched line(s) worth ₹50.00"
    )
    unmount()
    setup({
      ...withPosition,
      kpis: { ...withPosition.kpis, unverified: { count: 0, amount: "0.00" } },
    })
    renderWithProviders(<RemittanceDashboard module="prepaid" />)
    expect(screen.queryByTestId("unverified-notice")).not.toBeInTheDocument()
  })

  const CREDIT = {
    id: "credit-1",
    credit_date: "2026-11-24T00:00:00Z",
    amount: "700.00",
    reference: "BANKREF-9",
    narration: "NEFT",
    linked_amount: "300.00",
    statement_matched_amount: "100.00",
    manually_verified_amount: "200.00",
    unlinked_amount: "400.00",
    linked_references: "RB",
    link_state: "partially_linked",
    links: [
      {
        id: "l1",
        verification: "manually_verified",
        bank_reference: "UTR-55",
        reason: "Checked on the portal",
      },
    ],
  }

  async function openBankTab() {
    setup()
    vi.mocked(useBankCredits).mockReturnValue(ok({ data: [CREDIT], meta: META }) as never)
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(screen.getByRole("tab", { name: "Bank credit matching" }))
    return user
  }

  it("separates statement-matched from manually verified credits", async () => {
    await openBankTab()
    expect(screen.getByText("Statement-matched", { selector: "th" })).toBeInTheDocument()
    expect(screen.getByText("Manually verified", { selector: "th" })).toBeInTheDocument()
    expect(screen.getByText("UTR-55: Checked on the portal")).toBeInTheDocument()
    // Read-only users get no manual verification action.
    expect(screen.queryByRole("button", { name: "Link" })).not.toBeInTheDocument()
  })

  it("requires reference, bank reference, amount and reason before verifying", async () => {
    permissions.add("reconciliation.adjust")
    const user = await openBankTab()
    await user.click(screen.getByRole("button", { name: "Link" }))
    const dialog = await screen.findByRole("dialog")
    const submit = within(dialog).getByRole("button", { name: "Verify and link" })
    // Nothing is pre-filled for the verifier.
    for (const label of [
      "Reference",
      "Bank reference / UTR you verified",
      "Amount (₹)",
      "Reason",
    ]) {
      expect(within(dialog).getByLabelText(label)).toHaveValue("")
    }
    await user.type(within(dialog).getByLabelText("Reference"), "RB")
    await user.type(
      within(dialog).getByLabelText("Bank reference / UTR you verified"),
      "UTR-77"
    )
    await user.type(within(dialog).getByLabelText("Amount (₹)"), "400")
    expect(submit).toBeDisabled()
    await user.type(within(dialog).getByLabelText("Reason"), "  ")
    expect(submit).toBeDisabled()
    await user.type(within(dialog).getByLabelText("Reason"), "Portal check")
    expect(submit).toBeEnabled()
    await user.click(submit)
    expect(linkCredit).toHaveBeenCalledWith({
      bank_credit_id: "credit-1",
      reference: "RB",
      bank_reference: "UTR-77",
      amount: "400",
      reason: "Portal check",
    })
  })

  it("record detail explains the adjustment and labels bank evidence", async () => {
    setup(COD_SUMMARY, { data: [RECORD], meta: META })
    vi.mocked(useReconciliationDetail).mockReturnValue(
      ok({
        module: "cod",
        record: {
          ...RECORD,
          bank_status_label: "Partially Confirmed",
          bank_expected: "1000.00",
          bank_statement_amount: "100.00",
          bank_manual_amount: "200.00",
          bank_remaining: "700.00",
          bank_over: "0.00",
          adjustment_effect: "Manual adjustment +Rs 400.00 moves the status difference.",
          difference_before_adjustments: "-400.00",
          adjustment_amount: "400.00",
          difference: "0.00",
        },
        lines: [],
        adjustments: [],
        bank_references: [],
        bank_links: [
          {
            id: "a",
            reference: "RB",
            method: "manual",
            verification: "manually_verified",
            verification_label: "Manually verified",
            bank_reference: "UTR-55",
            reason: "Checked on the portal",
            linked_amount: "200.00",
            credit_amount: "700.00",
            credit_date: null,
            narration: null,
          },
          {
            id: "b",
            reference: "RB",
            method: "auto",
            verification: "statement_matched",
            verification_label: "Statement-matched",
            bank_reference: "RB",
            reason: null,
            linked_amount: "100.00",
            credit_amount: "100.00",
            credit_date: null,
            narration: null,
          },
        ],
      }) as never
    )
    const user = userEvent.setup()
    renderWithProviders(<RemittanceDashboard module="cod" />)
    await user.click(card("Short Settlement"))
    await user.click(within(screen.getByTestId("record-drilldown")).getByText("C3"))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByTestId("adjustment-effect")).toHaveTextContent(
      "moves the status difference"
    )
    for (const text of [
      "Partially Confirmed",
      "Total expected₹1,000.00",
      "Remaining amount₹700.00",
      "Difference before adjustments",
      "Manually verified",
      "Statement-matched",
      "Bank reference UTR-55 · Reason: Checked on the portal",
    ]) {
      expect(dialog).toHaveTextContent(text)
    }
  })
})
