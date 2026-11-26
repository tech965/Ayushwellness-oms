import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { FinanceDashboard } from "@/components/shiprocket-finance/finance-dashboard"
import { renderWithProviders } from "@/test-utils/render-with-providers"
import {
  downloadFinanceExport,
  useFinanceRecords,
  useFinanceSources,
  useFinanceSummary,
  usePayoutDetail,
  usePreviewFinanceImport,
  useShipmentCharges,
} from "@/services/shiprocket-finance"

const permissions = new Set<string>()
const importMutate = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/shiprocket-finance/expenses",
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ hasPermission: (code: string) => permissions.has(code) }),
}))
vi.mock("@/services/shiprocket-finance", () => ({
  useFinanceSources: vi.fn(),
  useFinanceSummary: vi.fn(),
  useFinanceRecords: vi.fn(),
  useShipmentCharges: vi.fn(),
  usePayoutDetail: vi.fn(),
  useSyncOrderFinance: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePreviewFinanceImport: vi.fn(),
  useFinanceImport: () => ({ mutateAsync: importMutate, isPending: false }),
  downloadFinanceExport: vi.fn(),
}))

const ok = <T,>(data: T) => ({ data, isLoading: false, isError: false, refetch: vi.fn() })
const card = (key: string, label: string, count: number, amount: string) => ({
  key,
  label,
  definition: `${label} means something precise.`,
  count,
  amount,
})
const EXPENSES = {
  cards: [
    card("net_cost", "Net Shipping Cost", 4, "140.00"),
    card("freight", "Freight Charges", 2, "150.00"),
    card("recharges", "Wallet Recharges", 1, "500.00"),
  ],
  transaction_count: 5,
  couriers: ["Delhivery"],
  wallet: { balance: "900.00", stale: false, ledger_sync_enabled: true },
}
const PAYOUTS = {
  cards: [
    card("net", "Net Remittance Reported", 4, "3990.00"),
    card("bank_matched", "Matched to Bank", 1, "900.00"),
  ],
  payout_count: 4,
  statuses: ["Paid", "Pending"],
  bank_data_available: true,
}
const META = { page: 1, page_size: 20, total_items: 1, total_pages: 1 }
const totals = (amount: string, count: number) => ({
  measure: "Freight",
  amount,
  count,
  base_amount: amount,
  base_count: count,
  breakdown: [],
})
const SOURCES = [
  {
    data: "Wallet transactions (freight, COD fee)",
    method: "api",
    endpoint: "GET /account/details/statement",
    state: "not_enabled",
    note: "Set the path first.",
  },
  {
    data: "COD payouts: CRF id, amounts, deductions, status",
    method: "report",
    endpoint: null,
    state: "report_only",
    note: "No Shiprocket API documents this.",
  },
  {
    data: "Shiprocket bills / freight invoices",
    method: "not_available",
    endpoint: null,
    state: "not_available",
    note: "No documented API.",
  },
]

function setup(kind: "expenses" | "remittances", records: unknown = undefined) {
  vi.mocked(useFinanceSummary).mockReturnValue(
    ok(kind === "expenses" ? EXPENSES : PAYOUTS) as never
  )
  vi.mocked(useFinanceRecords).mockReturnValue(ok(records) as never)
  vi.mocked(useFinanceSources).mockReturnValue(ok(SOURCES) as never)
  vi.mocked(useShipmentCharges).mockReturnValue(
    ok({
      data: [],
      meta: META,
      coverage: { orders_checked: 0, with_charges: 0 },
    }) as never
  )
  vi.mocked(usePayoutDetail).mockReturnValue(ok(undefined) as never)
  vi.mocked(usePreviewFinanceImport).mockReturnValue({
    mutateAsync: vi.fn(),
    isPending: false,
  } as never)
}

const lastArgs = () => vi.mocked(useFinanceRecords).mock.calls.at(-1)![0]
const cardButton = (label: string) =>
  within(screen.getByTestId("kpi-grid")).getByRole("button", {
    name: new RegExp(`^${label}`),
  })

describe("Shiprocket Expenses", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissions.clear()
  })

  it("makes every card clickable and opens its exact records", async () => {
    const user = userEvent.setup()
    setup("expenses", {
      data: [
        {
          id: "t1",
          transaction_ref: "TX1",
          txn_date: "2026-11-20T10:00:00Z",
          txn_type: "freight",
          direction: "debit",
          amount: "80.00",
          order_number: "#A1",
          awb: "AWB1",
          courier_name: "Delhivery",
          description: "Freight Charge",
          status: "confirmed",
          source: "shiprocket_api",
          contribution: "80.00",
        },
      ],
      meta: META,
      totals: totals("150.00", 2),
    })
    renderWithProviders(<FinanceDashboard kind="expenses" />)
    expect(lastArgs().kpi).toBeUndefined()
    for (const [label, key] of [
      ["Net Shipping Cost", "net_cost"],
      ["Freight Charges", "freight"],
      ["Wallet Recharges", "recharges"],
    ]) {
      await user.click(cardButton(label))
      expect(lastArgs()).toMatchObject({ kind: "expenses", kpi: key })
      expect(cardButton(label)).toHaveAttribute("aria-pressed", "true")
    }
    const box = screen.getByTestId("drilldown-totals")
    expect(box).toHaveTextContent("₹150.00")
    // The last card clicked (Wallet Recharges, ₹500.00) is compared with the 150 total shown.
    expect(screen.getByRole("alert")).toHaveTextContent("The card shows ₹500.00")
    expect(screen.getByTestId("drilldown-definition")).toHaveTextContent(
      "means something"
    )
  })

  it("says the figures reconcile, and warns when they do not", async () => {
    const user = userEvent.setup()
    setup("expenses", { data: [], meta: META, totals: totals("150.00", 2) })
    renderWithProviders(<FinanceDashboard kind="expenses" />)
    await user.click(cardButton("Freight Charges"))
    expect(screen.getByTestId("drilldown-totals")).toHaveTextContent(
      "Matches the card: ₹150.00 · 2 records"
    )
    setup("expenses", { data: [], meta: META, totals: totals("149.99", 2) })
    await user.click(cardButton("Net Shipping Cost"))
    expect(screen.getByRole("alert")).toHaveTextContent("The card shows ₹140.00")
  })

  it("exports exactly what is open and passes the filters through", async () => {
    const user = userEvent.setup()
    setup("expenses", { data: [], meta: META, totals: totals("150.00", 2) })
    renderWithProviders(<FinanceDashboard kind="expenses" />)
    await user.click(cardButton("Freight Charges"))
    await user.click(screen.getByRole("button", { name: "Export these records" }))
    expect(downloadFinanceExport).toHaveBeenCalledWith(
      "expenses",
      "freight",
      expect.objectContaining({ date_from: expect.any(String) })
    )
    await user.selectOptions(screen.getByLabelText("Courier"), "Delhivery")
    expect(lastArgs().filters.courier).toBe("Delhivery")
  })

  it("separates automatic data from report data, and never claims a missing feed", () => {
    setup("expenses")
    renderWithProviders(<FinanceDashboard kind="expenses" />)
    const panel = screen.getByTestId("sources")
    expect(panel).toHaveTextContent("Automatic (API)")
    expect(panel).toHaveTextContent("API path not enabled yet")
    expect(panel).toHaveTextContent("Report import")
    expect(panel).toHaveTextContent("Not available")
    expect(screen.getByText(/never added to the wallet totals/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/cashfree/i)
  })

  it("offers imports and fetching only to those allowed", () => {
    setup("expenses")
    const { unmount } = renderWithProviders(<FinanceDashboard kind="expenses" />)
    expect(screen.queryByRole("button", { name: /Import wallet report/ })).toBeNull()
    expect(screen.queryByRole("button", { name: /Fetch shipment charges/ })).toBeNull()
    unmount()
    permissions.add("reconciliation.upload")
    permissions.add("reconciliation.manage")
    renderWithProviders(<FinanceDashboard kind="expenses" />)
    expect(
      screen.getByRole("button", { name: /Import wallet report/ })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /Fetch shipment charges/ })
    ).toBeInTheDocument()
  })

  it("shows an empty drilldown with a zero total", async () => {
    const user = userEvent.setup()
    setup("expenses", {
      data: [],
      meta: { ...META, total_items: 0, total_pages: 0 },
      totals: totals("0.00", 0),
    })
    renderWithProviders(<FinanceDashboard kind="expenses" />)
    await user.click(cardButton("Freight Charges"))
    expect(screen.getByTestId("drilldown-total")).toHaveTextContent("₹0.00")
    expect(screen.getByText("No records match this selection")).toBeInTheDocument()
  })
})

describe("Shiprocket COD Remittances", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissions.clear()
  })

  const payout = {
    id: "p1",
    crf_id: "CRF1",
    utr: "UTR-A",
    payout_date: "2026-11-22T00:00:00Z",
    status: "Paid",
    cod_amount: "1000.00",
    freight_deduction: "80.00",
    other_deductions: "20.00",
    net_amount: "900.00",
    unexplained_difference: "0.00",
    bank_amount: "900.00",
    bank_state: "matched",
    bank_state_label: "Matched to bank",
    contribution: "900.00",
  }

  it("opens payouts, filters by status and bank result, and shows the bank comparison", async () => {
    const user = userEvent.setup()
    setup("remittances", { data: [payout], meta: META, totals: totals("900.00", 1) })
    renderWithProviders(<FinanceDashboard kind="remittances" />)
    await user.click(cardButton("Matched to Bank"))
    expect(lastArgs()).toMatchObject({ kind: "remittances", kpi: "bank_matched" })
    expect(screen.getByTestId("drilldown-totals")).toHaveTextContent(
      "Matches the card: ₹900.00 · 1 record"
    )
    const list = screen.getByTestId("record-drilldown")
    for (const text of ["CRF1", "UTR-A", "Matched to bank", "Bank ₹900.00"]) {
      expect(list).toHaveTextContent(text)
    }
    await user.selectOptions(screen.getByLabelText("Payout status"), "Paid")
    expect(lastArgs().filters.status).toBe("Paid")
    await user.selectOptions(screen.getByLabelText("Bank comparison"), "differs")
    expect(lastArgs().filters.bank_state).toBe("differs")
  })

  it("opens a payout's details on click", async () => {
    const user = userEvent.setup()
    setup("remittances", { data: [payout], meta: META, totals: totals("900.00", 1) })
    vi.mocked(usePayoutDetail).mockReturnValue(
      ok({
        payout,
        bank_credits: [
          { reference: "UTR-A", amount: "900.00", credit_date: null, narration: null },
        ],
        awb_lines: [],
        awb_lines_total: "0.00",
      }) as never
    )
    renderWithProviders(<FinanceDashboard kind="remittances" />)
    await user.click(cardButton("Net Remittance Reported"))
    await user.click(within(screen.getByTestId("record-drilldown")).getByText("CRF1"))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent("Bank credits with this UTR")
    expect(dialog).toHaveTextContent(
      "Per-AWB amounts come from the COD remittance report"
    )
  })

  it("says plainly when there is no bank data, and never calls that a match", () => {
    setup("remittances")
    vi.mocked(useFinanceSummary).mockReturnValue(
      ok({ ...PAYOUTS, bank_data_available: false }) as never
    )
    renderWithProviders(<FinanceDashboard kind="remittances" />)
    expect(screen.getByText(/No bank statement has been imported/)).toBeInTheDocument()
  })

  it("keeps Cashfree out of this module and offers the report fallback only with permission", () => {
    setup("remittances")
    const { unmount } = renderWithProviders(<FinanceDashboard kind="remittances" />)
    expect(screen.queryByRole("button", { name: /Import payout report/ })).toBeNull()
    expect(
      document.body.textContent?.replace(/Cashfree settlements are separate\./, "")
    ).not.toMatch(/cashfree/i)
    unmount()
    permissions.add("reconciliation.upload")
    renderWithProviders(<FinanceDashboard kind="remittances" />)
    expect(
      screen.getByRole("button", { name: /Import payout report/ })
    ).toBeInTheDocument()
  })
})
