/** Telecaller Order Detail — WhatsApp contact button.
 *
 * Kept separate from the page component so the phone-number
 * normalization (the one genuinely tricky part — getting the country
 * code right without duplicating it) is independently unit-testable.
 */

/** Strips everything except digits: spaces, "+", "-", brackets, and any
 * other formatting character. `null`/`undefined`/an all-non-digit string
 * all become `null` — never an empty-string URL downstream.
 */
function digitsOnly(phone: string): string | null {
  const digits = phone.replace(/\D/g, "")
  return digits.length > 0 ? digits : null
}

/** Normalizes a stored phone number into the bare digit string WhatsApp's
 * click-to-chat URL expects (country code + number, no leading "+").
 *
 * - A bare 10-digit Indian number (however it was formatted — spaces,
 *   dashes, brackets) gets "91" prepended.
 * - Anything longer than 10 digits already carries a country code (e.g.
 *   "+91 6304 824 438" -> "916304824438") and is used as-is — never
 *   prefixed again, so "919991234567" can never become "91919991234567".
 * - Anything else (too short, or not a phone number at all) is not a
 *   usable number — returns `null` rather than guessing.
 */
export function normalizePhoneForWhatsApp(phone: string | null | undefined): string | null {
  if (!phone) return null
  const digits = digitsOnly(phone)
  if (!digits) return null
  if (digits.length === 10) return `91${digits}`
  if (digits.length > 10) return digits
  return null
}

/** Builds the `https://wa.me/<digits>` click-to-chat URL, or `null` when
 * the phone number isn't usable (see `normalizePhoneForWhatsApp`) — the
 * caller disables/hides the button in that case rather than ever
 * navigating to a broken URL. Deliberately no pre-filled `?text=` — the
 * button only opens the conversation, never sends a message.
 */
export function buildWhatsAppUrl(phone: string | null | undefined): string | null {
  const normalized = normalizePhoneForWhatsApp(phone)
  return normalized ? `https://wa.me/${normalized}` : null
}
