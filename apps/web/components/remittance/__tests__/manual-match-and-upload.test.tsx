import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { ManualMatchDialog } from "@/components/remittance/manual-match-dialog"
import { mappingProblems, UploadDialog } from "@/components/remittance/upload-dialog"
import { renderWithProviders } from "@/test-utils/render-with-providers"
import type { MatchPreview, UnmatchedRow, UploadPreview } from "@/types/remittance"

const lookup = vi.fn()
const match = vi.fn()
const previewFile = vi.fn()
const uploadReport = vi.fn()
const uploadBank = vi.fn()
const template = vi.fn()

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock("@/services/remittance", () => ({
  useLookupOrderForLine: () => ({ mutateAsync: lookup, isPending: false }),
  useConfirmedMatch: () => ({ mutateAsync: match, isPending: false }),
  usePreviewUpload: () => ({ mutateAsync: previewFile, isPending: false }),
  useUploadRemittanceReport: () => ({ mutateAsync: uploadReport, isPending: false }),
  useUploadBankStatement: () => ({ mutateAsync: uploadBank, isPending: false }),
  downloadUploadTemplate: (...args: unknown[]) => template(...args),
}))

const LINE: UnmatchedRow = {
  kind: "remittance_line",
  id: "line-1",
  partner: "Delhivery",
  awb: "AWB-STRAY",
  order_ref: null,
  reference: "RM-9",
  amount: "600.00",
  date: "2026-11-20T00:00:00Z",
  reason: "No order found for this AWB / order reference",
}

function preview(overrides: Partial<MatchPreview> = {}): MatchPreview {
  return {
    order_id: "o-1",
    order_number: "C-100",
    awb: "AWB-100",
    customer_name: "Asha",
    expected_amount: "1000.00",
    line_amount: "600.00",
    other_remitted: "0.00",
    adjustment_amount: "0.00",
    projected_total: "600.00",
    projected_difference: "-400.00",
    projected_status: "short",
    projected_status_label: "Short",
    blockers: [],
    warnings: [
      "Partial settlement: after this match the order is still short by 400.00.",
    ],
    needs_acknowledgement: true,
    ...overrides,
  }
}

async function find(user: ReturnType<typeof userEvent.setup>, text = "C-100") {
  await user.type(screen.getByLabelText("OMS order ID or AWB"), text)
  await user.click(screen.getByRole("button", { name: "Find order" }))
}

describe("ManualMatchDialog", () => {
  beforeEach(() => vi.clearAllMocks())

  it("shows the remittance line being matched", () => {
    renderWithProviders(<ManualMatchDialog line={LINE} open onOpenChange={vi.fn()} />)
    const box = screen.getByTestId("match-line")
    expect(box).toHaveTextContent("Delhivery")
    expect(box).toHaveTextContent("AWB-STRAY")
    expect(box).toHaveTextContent("RM-9")
    expect(box).toHaveTextContent("₹600.00")
  })

  it("searches by the exact text typed and offers no fuzzy results", async () => {
    lookup.mockResolvedValue({ found: false, preview: null })
    const user = userEvent.setup()
    renderWithProviders(<ManualMatchDialog line={LINE} open onOpenChange={vi.fn()} />)
    await find(user, "C-10")
    expect(lookup).toHaveBeenCalledWith({ line_id: "line-1", q: "C-10" })
    expect(screen.getByRole("alert")).toHaveTextContent("exactly that order ID or AWB")
    expect(screen.getByRole("button", { name: "Match remittance" })).toBeDisabled()
  })

  it("shows expected COD, the remittance, other remittances, difference and resulting status", async () => {
    lookup.mockResolvedValue({ found: true, preview: preview() })
    const user = userEvent.setup()
    renderWithProviders(<ManualMatchDialog line={LINE} open onOpenChange={vi.fn()} />)
    await find(user)
    const box = await screen.findByTestId("match-preview")
    expect(box).toHaveTextContent("C-100")
    expect(box).toHaveTextContent("Expected COD amount₹1,000.00")
    expect(box).toHaveTextContent("This remittance₹600.00")
    expect(box).toHaveTextContent("Already remitted on this order₹0.00")
    expect(box).toHaveTextContent("Total after match₹600.00")
    expect(box).toHaveTextContent("Difference from expected-₹400.00")
    expect(box).toHaveTextContent("After match: Short")
    expect(box).toHaveTextContent("Partial settlement")
  })

  it("requires confirmation, and acceptance of a difference, before saving", async () => {
    lookup.mockResolvedValue({ found: true, preview: preview() })
    match.mockResolvedValue(undefined)
    const onOpenChange = vi.fn()
    const user = userEvent.setup()
    renderWithProviders(
      <ManualMatchDialog line={LINE} open onOpenChange={onOpenChange} />
    )
    await find(user)
    await screen.findByTestId("match-preview")
    const save = screen.getByRole("button", { name: "Match remittance" })
    expect(save).toBeDisabled()
    await user.click(screen.getByLabelText(/I confirm this remittance/))
    expect(save).toBeDisabled() // still needs the difference accepted
    await user.click(screen.getByLabelText(/I accept the difference of -₹400.00/))
    expect(save).toBeEnabled()
    await user.click(save)
    await waitFor(() =>
      expect(match).toHaveBeenCalledWith({
        line_id: "line-1",
        order_id: "o-1",
        acknowledge_difference: true,
      })
    )
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it("an exact match only needs the confirmation", async () => {
    lookup.mockResolvedValue({
      found: true,
      preview: preview({
        line_amount: "1000.00",
        projected_total: "1000.00",
        projected_difference: "0.00",
        projected_status: "partially_reconciled",
        projected_status_label: "Partially Reconciled",
        warnings: [],
        needs_acknowledgement: false,
      }),
    })
    match.mockResolvedValue(undefined)
    const user = userEvent.setup()
    renderWithProviders(<ManualMatchDialog line={LINE} open onOpenChange={vi.fn()} />)
    await find(user)
    await screen.findByTestId("match-preview")
    expect(screen.queryByLabelText(/I accept the difference/)).not.toBeInTheDocument()
    await user.click(screen.getByLabelText(/I confirm this remittance/))
    await user.click(screen.getByRole("button", { name: "Match remittance" }))
    await waitFor(() =>
      expect(match).toHaveBeenCalledWith(
        expect.objectContaining({ acknowledge_difference: false })
      )
    )
  })

  it("a blocked order (not COD / not delivered / already matched) cannot be confirmed", async () => {
    lookup.mockResolvedValue({
      found: true,
      preview: preview({
        blockers: [
          "This order has not been delivered, so a COD remittance cannot be matched to it.",
        ],
      }),
    })
    const user = userEvent.setup()
    renderWithProviders(<ManualMatchDialog line={LINE} open onOpenChange={vi.fn()} />)
    await find(user)
    const box = await screen.findByTestId("match-preview")
    expect(within(box).getByRole("alert")).toHaveTextContent("not been delivered")
    expect(screen.queryByLabelText(/I confirm this remittance/)).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Match remittance" })).toBeDisabled()
    expect(match).not.toHaveBeenCalled()
  })

  it("changing the search clears the previous result and the ticked boxes", async () => {
    lookup.mockResolvedValue({ found: true, preview: preview() })
    const user = userEvent.setup()
    renderWithProviders(<ManualMatchDialog line={LINE} open onOpenChange={vi.fn()} />)
    await find(user)
    await screen.findByTestId("match-preview")
    await user.click(screen.getByLabelText(/I confirm this remittance/))
    await user.type(screen.getByLabelText("OMS order ID or AWB"), "x")
    expect(screen.queryByTestId("match-preview")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Match remittance" })).toBeDisabled()
  })
})

function uploadPreview(overrides: Partial<UploadPreview> = {}): UploadPreview {
  return {
    headers: ["AWB", "Remittance Amount", "Notes"],
    suggested_mapping: { awb: "AWB", remittance_amount: "Remittance Amount" },
    fields: [
      { field: "awb", label: "AWB number", template_header: "AWB" },
      { field: "order_ref", label: "OMS order number", template_header: "OMS Order ID" },
      {
        field: "remittance_amount",
        label: "Remittance amount",
        template_header: "Remittance Amount",
      },
    ],
    required_groups: [["remittance_amount"], ["awb", "order_ref"]],
    missing: [],
    ambiguous: {},
    ignored: ["Notes"],
    sample_rows: [["A1", "100", "hi"]],
    can_import: true,
    problems: [],
    ...overrides,
  }
}

const FILE = new File(["x"], "report.csv", { type: "text/csv" })

async function pickFile(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Shipping partner"), "Delhivery")
  await user.upload(screen.getByLabelText("File"), FILE)
}

describe("UploadDialog column mapping", () => {
  beforeEach(() => vi.clearAllMocks())

  it("reads the file first and shows what it will use, without importing", async () => {
    previewFile.mockResolvedValue(uploadPreview())
    const user = userEvent.setup()
    renderWithProviders(
      <UploadDialog module="cod" kind="report" open onOpenChange={vi.fn()} />
    )
    await pickFile(user)
    const mapping = await screen.findByTestId("column-mapping")
    expect(previewFile).toHaveBeenCalledWith(FILE)
    expect(within(mapping).getByLabelText("AWB number")).toHaveValue("AWB")
    expect(within(mapping).getByLabelText("Remittance amount")).toHaveValue(
      "Remittance Amount"
    )
    expect(within(mapping).getByLabelText("OMS order number")).toHaveValue("__none__")
    expect(mapping).toHaveTextContent("Not used: Notes")
    expect(uploadReport).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Import" })).toBeEnabled()
  })

  it("an unrecognised file cannot be imported until the user maps the required columns", async () => {
    previewFile.mockResolvedValue(
      uploadPreview({
        headers: ["Order ID", "Paid"],
        suggested_mapping: {},
        ignored: ["Order ID", "Paid"],
        problems: ["No column was identified for: Remittance amount."],
        can_import: false,
      })
    )
    const user = userEvent.setup()
    renderWithProviders(
      <UploadDialog module="cod" kind="report" open onOpenChange={vi.fn()} />
    )
    await pickFile(user)
    const mapping = await screen.findByTestId("column-mapping")
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled()
    expect(mapping).toHaveTextContent("Choose a column for: Remittance amount.")
    expect(mapping).toHaveTextContent(
      "Choose a column for: AWB number or OMS order number."
    )
    fireEvent.change(within(mapping).getByLabelText("Remittance amount"), {
      target: { value: "Paid" },
    })
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled() // order ref still missing
    fireEvent.change(within(mapping).getByLabelText("OMS order number"), {
      target: { value: "Order ID" },
    })
    expect(screen.getByRole("button", { name: "Import" })).toBeEnabled()
  })

  it("sends the chosen mapping with the file and shows the columns used", async () => {
    previewFile.mockResolvedValue(
      uploadPreview({
        headers: ["Order ID", "Paid"],
        suggested_mapping: {},
        can_import: false,
      })
    )
    uploadReport.mockResolvedValue({
      file_id: "f1",
      filename: "report.csv",
      rows_read: 1,
      accepted: 1,
      duplicates_skipped: 0,
      rejected: 0,
      auto_matched_to_orders: 1,
      unmatched: 0,
      bank_credits_linked: 0,
      row_errors: [],
      column_mapping: { order_ref: "Order ID", remittance_amount: "Paid" },
      ignored_columns: ["Notes"],
    })
    const user = userEvent.setup()
    renderWithProviders(
      <UploadDialog module="cod" kind="report" open onOpenChange={vi.fn()} />
    )
    await pickFile(user)
    const mapping = await screen.findByTestId("column-mapping")
    fireEvent.change(within(mapping).getByLabelText("Remittance amount"), {
      target: { value: "Paid" },
    })
    fireEvent.change(within(mapping).getByLabelText("OMS order number"), {
      target: { value: "Order ID" },
    })
    await user.click(screen.getByRole("button", { name: "Import" }))
    await waitFor(() =>
      expect(uploadReport).toHaveBeenCalledWith({
        name: "Delhivery",
        file: FILE,
        columnMap: { remittance_amount: "Paid", order_ref: "Order ID" },
      })
    )
    const result = await screen.findByTestId("upload-result")
    expect(result).toHaveTextContent(
      "Columns used: order_ref ← Order ID · remittance_amount ← Paid"
    )
    expect(result).toHaveTextContent("Ignored: Notes")
  })

  it("flags an ambiguous field and blocks the import until one column is chosen", async () => {
    previewFile.mockResolvedValue(
      uploadPreview({
        headers: ["AWB", "Remittance Amount", "Amount Remitted"],
        suggested_mapping: { awb: "AWB" },
        ambiguous: { remittance_amount: ["Remittance Amount", "Amount Remitted"] },
        problems: ["Several columns could be 'Remittance amount'."],
        can_import: false,
      })
    )
    const user = userEvent.setup()
    renderWithProviders(
      <UploadDialog module="cod" kind="report" open onOpenChange={vi.fn()} />
    )
    await pickFile(user)
    const mapping = await screen.findByTestId("column-mapping")
    expect(mapping).toHaveTextContent("ambiguous")
    expect(mapping).toHaveTextContent("More than one column could be Remittance amount")
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled()
    fireEvent.change(within(mapping).getByLabelText("Remittance amount"), {
      target: { value: "Amount Remitted" },
    })
    expect(screen.getByRole("button", { name: "Import" })).toBeEnabled()
  })

  it("offers the template download for this upload type", async () => {
    const user = userEvent.setup()
    renderWithProviders(
      <UploadDialog module="prepaid" kind="report" open onOpenChange={vi.fn()} />
    )
    template.mockResolvedValue(undefined)
    await user.click(screen.getByRole("button", { name: "Download template" }))
    expect(template).toHaveBeenCalledWith("prepaid", "report")
  })

  it("uses the bank-statement upload for a statement", async () => {
    previewFile.mockResolvedValue(uploadPreview())
    uploadBank.mockResolvedValue({
      file_id: "f2",
      filename: "b.csv",
      rows_read: 1,
      accepted: 1,
      duplicates_or_non_credits_skipped: 0,
      rejected: 0,
      auto_linked_cod: 1,
      auto_linked_prepaid: 0,
      row_errors: [],
    })
    const user = userEvent.setup()
    renderWithProviders(
      <UploadDialog module="cod" kind="bank" open onOpenChange={vi.fn()} />
    )
    await user.upload(screen.getByLabelText("File"), FILE)
    await screen.findByTestId("column-mapping")
    await user.click(screen.getByRole("button", { name: "Import" }))
    await waitFor(() => expect(uploadBank).toHaveBeenCalled())
    expect(uploadReport).not.toHaveBeenCalled()
  })

  it("mappingProblems mirrors the server's required groups", () => {
    const p = uploadPreview()
    expect(mappingProblems(p, {})).toHaveLength(2)
    expect(mappingProblems(p, { remittance_amount: "x" })).toEqual([
      "Choose a column for: AWB number or OMS order number.",
    ])
    expect(mappingProblems(p, { remittance_amount: "x", awb: "y" })).toEqual([])
    expect(mappingProblems(p, { remittance_amount: "x", order_ref: "z" })).toEqual([])
  })
})
