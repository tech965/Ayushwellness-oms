"use client"

import * as React from "react"
import { toast } from "sonner"

import { mappingProblems } from "@/components/remittance/upload-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { getApiErrorMessage } from "@/lib/api-client"
import { useFinanceImport, usePreviewFinanceImport } from "@/services/shiprocket-finance"
import type {
  FinanceImportResult,
  FinanceKind,
  UploadPreview,
} from "@/types/shiprocket-finance"

/** Importing a Shiprocket report, the supported fallback where no API
 * provides the data. The file is read first and nothing is imported until the
 * columns are confirmed; a column is never guessed.
 */
export function ReportImportDialog({
  kind,
  onClose,
}: {
  kind: FinanceKind
  onClose: () => void
}) {
  const isExpenses = kind === "expenses"
  const [file, setFile] = React.useState<File | null>(null)
  const [preview, setPreview] = React.useState<UploadPreview | null>(null)
  const [mapping, setMapping] = React.useState<Record<string, string>>({})
  const [result, setResult] = React.useState<FinanceImportResult | null>(null)
  const previewMutation = usePreviewFinanceImport(kind)
  const importMutation = useFinanceImport(kind)
  const problems = preview ? mappingProblems(preview, mapping) : []

  async function choose(next: File | null) {
    setFile(next)
    setPreview(null)
    setResult(null)
    if (!next) return
    try {
      const outcome = await previewMutation.mutateAsync(next)
      setPreview(outcome)
      setMapping({ ...outcome.suggested_mapping })
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  async function submit() {
    if (!file) return
    try {
      setResult(await importMutation.mutateAsync({ file, columnMap: mapping }))
      toast.success("File imported.")
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {isExpenses
              ? "Import Shiprocket wallet report"
              : "Import Shiprocket payout report"}
          </DialogTitle>
          <DialogDescription>
            CSV or Excel (.xlsx), up to 5 MB, exported from the Shiprocket dashboard. Rows
            already imported are skipped, and the same file is never imported twice.
            {isExpenses
              ? " A movement already received from the API is not counted again."
              : " A later report can fill blanks or update a status, but never changes a reported amount or UTR."}
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div
            className="space-y-1 rounded-lg border p-3 text-sm"
            data-testid="import-result"
          >
            <p>Rows read: {result.rows_read}</p>
            <p>Added: {result.added}</p>
            {result.updated > 0 && <p>Updated: {result.updated}</p>}
            <p>Already imported: {result.duplicates_skipped}</p>
            {result.conflicts > 0 && (
              <p className="text-amber-700 dark:text-amber-400">
                Refused (differs from what is stored): {result.conflicts}
              </p>
            )}
            <p>Rejected rows: {result.rejected}</p>
            {result.row_errors.slice(0, 10).map((e) => (
              <p key={e.row} className="text-muted-foreground text-xs">
                Row {e.row}: {e.error}
              </p>
            ))}
          </div>
        ) : (
          <div className="space-y-3">
            <input
              type="file"
              accept=".csv,.xlsx"
              aria-label="Report file"
              onChange={(e) => void choose(e.target.files?.[0] ?? null)}
            />
            {preview && (
              <div className="space-y-2">
                <p className="text-sm font-medium">Columns found in the file</p>
                {preview.fields.map((f) => (
                  <label
                    key={f.field}
                    className="flex items-center justify-between gap-3 text-sm"
                  >
                    <span>{f.label}</span>
                    <select
                      className="bg-background h-8 max-w-56 rounded-md border px-2"
                      aria-label={f.label}
                      value={mapping[f.field] ?? ""}
                      onChange={(e) => {
                        const next = { ...mapping }
                        if (e.target.value) next[f.field] = e.target.value
                        else delete next[f.field]
                        setMapping(next)
                      }}
                    >
                      <option value="">Not in the file</option>
                      {preview.headers.map((h) => (
                        <option key={h} value={h}>
                          {h}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
                {problems.map((p) => (
                  <p
                    key={p}
                    role="alert"
                    className="text-sm text-amber-700 dark:text-amber-400"
                  >
                    {p}
                  </p>
                ))}
              </div>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {result ? "Close" : "Cancel"}
          </Button>
          {!result && (
            <Button
              onClick={() => void submit()}
              disabled={!preview || problems.length > 0 || importMutation.isPending}
            >
              {importMutation.isPending ? "Importing…" : "Import"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
