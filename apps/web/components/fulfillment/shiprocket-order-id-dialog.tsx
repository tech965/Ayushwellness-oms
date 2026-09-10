"use client"

import * as React from "react"
import { Copy } from "lucide-react"
import { toast } from "sonner"

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
import { copyToClipboard } from "@/lib/shiprocket"

export interface ShiprocketOrderIdDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The real, numeric Shiprocket order id to display -- `null` only
   * momentarily while a dialog opened optimistically is still waiting on
   * its id (callers should generally not open this until they actually
   * have one).
   */
  orderId: string | null
}

/** Shown by "Process Shipment"/"Ship Order" right after they open
 * Shiprocket's plain "Ready to Ship" page in a new tab (Shiprocket has
 * no supported deep-link filter for one specific order -- see
 * `SHIPROCKET_READY_TO_SHIP_URL`'s docstring in `lib/shiprocket.ts`).
 * Displays the id as visible, selectable text -- a manual Ctrl+C
 * fallback -- plus a dedicated "Copy Order ID" button.
 *
 * The clipboard write is DELIBERATELY only ever triggered by this
 * button's own `onClick`, with nothing else in that handler -- never
 * automatically right after `window.open()` or a `locate` network
 * response resolves. Real production incident this fixes: Chrome/Edge's
 * Async Clipboard API requires the calling document to be focused at the
 * moment `navigator.clipboard.writeText()` actually runs;
 * `window.open(url, "_blank")` typically hands focus to the new tab, and
 * a clipboard write chained inside an async `onSuccess` callback (after
 * a real network round trip) runs well outside the original click's
 * user-activation window either way -- both silently failed with
 * `NotAllowedError` in practice, which `copyToClipboard`'s `catch`
 * swallowed into an unhelpful "couldn't copy automatically" toast on
 * every single attempt. A fresh, undelayed click on this dialog's own
 * button sidesteps both causes.
 */
export function ShiprocketOrderIdDialog({ open, onOpenChange, orderId }: ShiprocketOrderIdDialogProps) {
  const inputRef = React.useRef<HTMLInputElement>(null)

  async function handleCopy() {
    if (!orderId) return
    const copied = await copyToClipboard(orderId)
    if (copied) {
      toast.success(`Shiprocket Order ID ${orderId} copied.`)
    } else {
      inputRef.current?.select()
      toast.warning("Couldn't copy automatically.", {
        description: "The ID below is selected -- press Ctrl+C (or Cmd+C) to copy it manually.",
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Shiprocket Order ID</DialogTitle>
          <DialogDescription>
            Shiprocket&apos;s Ready to Ship page opened in a new tab. Paste this ID into its
            Multiple Order IDs filter to find this order.
          </DialogDescription>
        </DialogHeader>
        <Input
          ref={inputRef}
          readOnly
          value={orderId ?? ""}
          onFocus={(e) => e.currentTarget.select()}
          className="text-center font-mono text-base"
          aria-label="Shiprocket Order ID"
        />
        <DialogFooter>
          <Button onClick={handleCopy} disabled={!orderId}>
            <Copy className="size-3.5" />
            Copy Order ID
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
