import { MessageCircle } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { buildWhatsAppUrl } from "@/lib/whatsapp"

interface WhatsAppButtonProps {
  phone: string | null | undefined
  className?: string
}

/** Compact icon-button that opens a WhatsApp click-to-chat conversation
 * for the customer's phone number (Telecaller Order Detail's "Customer &
 * Order" card, right beside the phone number). No new icon dependency —
 * lucide-react ships no branded WhatsApp mark, so `MessageCircle` (its
 * closest generic chat-bubble icon) stands in, tinted WhatsApp's brand
 * green so it still reads as "WhatsApp" at a glance in both themes.
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
          className={cn(
            "text-[#25D366] hover:bg-[#25D366]/10 hover:text-[#25D366] dark:hover:bg-[#25D366]/15",
            className
          )}
          onClick={() => window.open(url, "_blank", "noopener,noreferrer")}
        >
          <MessageCircle />
        </Button>
      </TooltipTrigger>
      <TooltipContent>Open WhatsApp chat</TooltipContent>
    </Tooltip>
  )
}
