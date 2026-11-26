"use client"

import * as React from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { getApiErrorMessage } from "@/lib/api-client"
import { SUCCESS_STATUSES } from "@/lib/remittance-definitions"
import {
  downloadUploadTemplate,
  usePreviewUpload,
  useUploadBankStatement,
  useUploadRemittanceReport,
} from "@/services/remittance"
import type {
  RemittanceModule,
  UploadKind,
  UploadPreview,
  UploadResult,
} from "@/types/remittance"

export type { UploadKind } from "@/types/remittance"

const NONE = "__none__"

interface UploadDialogProps {
  module: RemittanceModule
  kind: UploadKind
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Known partner / gateway names, offered as suggestions. */
  suggestions?: string[]
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  )
}

/** Each inner group needs at least one mapped field (mirrors the server). */
export function mappingProblems(
  preview: UploadPreview,
  mapping: Record<string, string>
): string[] {
  const label = (field: string) =>
    preview.fields.find((f) => f.field === field)?.label ?? field
  return preview.required_groups
    .filter((group) => !group.some((field) => mapping[field]))
    .map((group) => `Choose a column for: ${group.map(label).join(" or ")}.`)
}

/** Uploads a partner COD remittance report, a gateway settlement report or a
 * bank statement in two steps: the server first READS the file (nothing is
 * imported) and shows which column it will use for each field, which columns
 * it ignores, and what is missing or ambiguous; the user can pick the right
 * column for any field. Only then is the file imported. The original file is
 * stored unchanged by the server.
 */
export function UploadDialog({
  module,
  kind,
  open,
  onOpenChange,
  suggestions = [],
}: UploadDialogProps) {
  const isBank = kind === "bank"
  const [name, setName] = React.useState(isBank ? "Bank statement" : "")
  const [file, setFile] = React.useState<File | null>(null)
  const [preview, setPreview] = React.useState<UploadPreview | null>(null)
  const [mapping, setMapping] = React.useState<Record<string, string>>({})
  const [result, setResult] = React.useState<UploadResult | null>(null)
  const previewMutation = usePreviewUpload(module, kind)
  const reportUpload = useUploadRemittanceReport(module)
  const bankUpload = useUploadBankStatement()
  const importing = reportUpload.isPending || bankUpload.isPending

  const nameLabel = isBank
    ? "Bank / account name"
    : module === "cod"
      ? "Shipping partner"
      : "Payment gateway"
  const title = isBank
    ? "Upload bank statement"
    : module === "cod"
      ? "Upload COD remittance report"
      : "Upload gateway settlement report"

  function reset() {
    setFile(null)
    setPreview(null)
    setMapping({})
    setResult(null)
    if (!isBank) setName("")
  }

  async function chooseFile(next: File | null) {
    setFile(next)
    setPreview(null)
    setMapping({})
    if (!next) return
    try {
      const outcome = await previewMutation.mutateAsync(next)
      setPreview(outcome)
      setMapping({ ...outcome.suggested_mapping })
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  const problems = preview ? mappingProblems(preview, mapping) : []
  const ambiguousUnresolved = preview
    ? Object.keys(preview.ambiguous).filter((field) => !mapping[field])
    : []

  async function submit() {
    if (!file || !preview) return
    try {
      const input = { name, file, columnMap: mapping }
      const outcome = isBank
        ? await bankUpload.mutateAsync(input)
        : await reportUpload.mutateAsync(input)
      setResult(outcome)
      toast.success("File imported.")
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) reset()
      }}
    >
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            CSV or Excel (.xlsx), up to 5 MB. The file is read first and nothing is
            imported until you confirm the columns. The original file is kept unchanged,
            rows already imported are skipped, and nothing is marked reconciled without
            matching evidence.
            {module === "prepaid" &&
              kind === "report" &&
              ` A settlement counts only when its Status is one of: ${SUCCESS_STATUSES.join(", ")}. Blank or unfamiliar statuses stay Unverified.`}
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3" data-testid="upload-result">
            <div className="space-y-1.5 rounded-lg border p-3">
              <Row label="Rows read" value={result.rows_read} />
              <Row label="Imported" value={result.accepted} />
              <Row
                label="Duplicates / non-credit rows skipped"
                value={
                  result.duplicates_skipped ??
                  result.duplicates_or_non_credits_skipped ??
                  0
                }
              />
              <Row label="Rejected rows" value={result.rejected} />
              {Boolean(result.unsuccessful_rows) && (
                <Row
                  label="Not successful (not counted)"
                  value={result.unsuccessful_rows}
                />
              )}
              {Boolean(result.unverified_rows) && (
                <Row label="Unverified (not counted)" value={result.unverified_rows} />
              )}
              {Boolean(result.statuses_updated) && (
                <Row
                  label="Unverified rows given a status"
                  value={result.statuses_updated}
                />
              )}
              {result.auto_matched_to_orders !== null &&
                result.auto_matched_to_orders !== undefined && (
                  <>
                    <Row
                      label="Matched to orders"
                      value={result.auto_matched_to_orders}
                    />
                    <Row
                      label="Not matched (kept visible)"
                      value={result.unmatched ?? 0}
                    />
                    <Row
                      label="Bank credits linked"
                      value={result.bank_credits_linked ?? 0}
                    />
                  </>
                )}
              {result.auto_linked_cod !== null &&
                result.auto_linked_cod !== undefined && (
                  <>
                    <Row
                      label="Linked to COD remittances"
                      value={result.auto_linked_cod}
                    />
                    <Row
                      label="Linked to prepaid settlements"
                      value={result.auto_linked_prepaid ?? 0}
                    />
                  </>
                )}
            </div>
            {result.column_mapping && Object.keys(result.column_mapping).length > 0 && (
              <p className="text-muted-foreground text-xs">
                Columns used:{" "}
                {Object.entries(result.column_mapping)
                  .map(([field, header]) => `${field} ← ${header}`)
                  .join(" · ")}
                {result.ignored_columns && result.ignored_columns.length > 0
                  ? `. Ignored: ${result.ignored_columns.join(", ")}.`
                  : "."}
              </p>
            )}
            {result.row_errors.length > 0 && (
              <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs dark:bg-amber-950">
                <p className="mb-1 font-medium">
                  Rejected rows (first {result.row_errors.length})
                </p>
                <ul className="max-h-32 space-y-0.5 overflow-y-auto">
                  {result.row_errors.slice(0, 20).map((e) => (
                    <li key={`${e.row}-${e.error}`}>
                      Row {e.row}: {e.error}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault()
              void submit()
            }}
          >
            <div className="space-y-1.5">
              <Label htmlFor="upload-name">{nameLabel}</Label>
              <Input
                id="upload-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                list="upload-name-suggestions"
                placeholder={
                  isBank ? "e.g. HDFC current account" : "Name as in the report"
                }
                required
              />
              <datalist id="upload-name-suggestions">
                {suggestions.map((s) => (
                  <option key={s} value={s} />
                ))}
              </datalist>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="upload-file">File</Label>
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  className="h-auto p-0 text-xs"
                  onClick={() =>
                    void downloadUploadTemplate(module, kind).catch((e) =>
                      toast.error(getApiErrorMessage(e))
                    )
                  }
                >
                  Download template
                </Button>
              </div>
              <Input
                id="upload-file"
                type="file"
                accept=".csv,.xlsx"
                onChange={(event) => void chooseFile(event.target.files?.[0] ?? null)}
              />
              {previewMutation.isPending && (
                <p className="text-muted-foreground text-xs">Reading the file…</p>
              )}
            </div>

            {preview && (
              <section
                className="space-y-3"
                data-testid="column-mapping"
                aria-label="Column mapping"
              >
                <div>
                  <h4 className="text-sm font-semibold">Columns</h4>
                  <p className="text-muted-foreground text-xs">
                    Check which column of your file is used for each field. Nothing is
                    guessed: only the OMS template headers and names this system already
                    uses are matched automatically.
                  </p>
                </div>
                <div className="divide-y rounded-lg border">
                  {preview.fields.map((f) => {
                    const ambiguous = preview.ambiguous[f.field]
                    const group = preview.required_groups.find((g) => g.includes(f.field))
                    const required = group?.length === 1
                    const oneOf = Boolean(group && group.length > 1)
                    return (
                      <div
                        key={f.field}
                        className="grid items-center gap-2 p-2 sm:grid-cols-[1fr_1.2fr]"
                      >
                        <Label htmlFor={`map-${f.field}`} className="text-sm font-normal">
                          {f.label}
                          {required && <span className="text-red-600"> *</span>}
                          {oneOf && (
                            <Badge variant="outline" className="ml-2 text-[10px]">
                              one of these required
                            </Badge>
                          )}
                          {ambiguous && (
                            <Badge variant="outline" className="ml-2 text-[10px]">
                              ambiguous
                            </Badge>
                          )}
                        </Label>
                        <select
                          id={`map-${f.field}`}
                          aria-label={f.label}
                          className="border-input bg-background h-9 rounded-md border px-2 text-sm"
                          value={mapping[f.field] ?? NONE}
                          onChange={(event) =>
                            setMapping((current) => {
                              const next = { ...current }
                              if (event.target.value === NONE) delete next[f.field]
                              else next[f.field] = event.target.value
                              return next
                            })
                          }
                        >
                          <option value={NONE}>— not in this file —</option>
                          {preview.headers.map((header) => (
                            <option key={header} value={header}>
                              {header}
                            </option>
                          ))}
                        </select>
                      </div>
                    )
                  })}
                </div>
                {preview.ignored.length > 0 && (
                  <p className="text-muted-foreground text-xs">
                    Not used: {preview.ignored.join(", ")}
                  </p>
                )}
                {ambiguousUnresolved.length > 0 && (
                  <p className="text-xs text-amber-700 dark:text-amber-400" role="alert">
                    More than one column could be{" "}
                    {ambiguousUnresolved
                      .map(
                        (field) =>
                          `${preview.fields.find((x) => x.field === field)?.label} (${preview.ambiguous[field].join(", ")})`
                      )
                      .join("; ")}
                    . Choose one.
                  </p>
                )}
                {problems.length > 0 && (
                  <ul className="text-xs text-red-700 dark:text-red-400" role="alert">
                    {problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                )}
                {preview.sample_rows.length > 0 && (
                  <div className="overflow-x-auto rounded-lg border">
                    <table className="w-full text-xs">
                      <thead className="bg-muted/40">
                        <tr>
                          {preview.headers.map((h) => (
                            <th key={h} className="px-2 py-1 text-left font-medium">
                              {h}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {preview.sample_rows.map((row, i) => (
                          <tr key={i} className="border-t">
                            {row.map((cell, j) => (
                              <td key={j} className="px-2 py-1">
                                {cell}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            )}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={
                  !file ||
                  !preview ||
                  !name.trim() ||
                  importing ||
                  problems.length > 0 ||
                  ambiguousUnresolved.length > 0
                }
              >
                {importing ? "Importing…" : "Import"}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
