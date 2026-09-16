import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { buildWhatsAppUrl } from "@/lib/whatsapp"

import { WhatsAppIcon } from "./whatsapp-icon"

interface WhatsAppButtonProps {
  phone: string | null | undefined
  className?: string
}

/** Compact icon-button that opens a WhatsApp click-to-chat conversation
 * for the customer's phone number (Telecaller Order Detail's "Customer &
 * Order" card, right beside the phone number). Renders the actual
 * WhatsApp brand glyph (`WhatsAppIcon` — an inline SVG, no new icon-
 * library dependency), sized at 24px (`size-6`) so it's clearly
 * recognizable beside the phone number rather than the tiny generic
 * chat-bubble icon this used to be.
 *
 * Renders nothing when the phone number isn't usable (missing, or too
 * short/malformed to normalize) — never a button that opens a broken
 * `wa.me` URL, and never a crash.
 */
export function WhatsAppButton({ phone, className }: WhatsAppButtonProps) {
  const url = buildWhatsAppUrl(phone)
  if (!url) return null

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Open WhatsApp chat with customer"
          className={cn("hover:bg-[#25D366]/10", className)}
          onClick={() => window.open(url, "_blank", "noopener,noreferrer")}
        >
          <WhatsAppIcon className="size-6" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>Open WhatsApp chat</TooltipContent>
    </Tooltip>
  )
}
