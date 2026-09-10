/** Shiprocket support confirmed there is no officially supported
 * deep-link URL for opening one specific order on their "Ready to Ship"
 * page -- an earlier version of this feature tried
 * `?order_ids={id}` and Shiprocket's own page silently ignored/reset it
 * (mirrors `SHIPROCKET_READY_TO_SHIP_URL` in
 * `app.services.shiprocket_service` on the backend; keep both in sync).
 * The supported workflow: open this PLAIN page, no query string ever,
 * and copy the real Shiprocket order id to the clipboard for the
 * operator to paste into Shiprocket's own "Multiple Order IDs" filter.
 */
export const SHIPROCKET_READY_TO_SHIP_URL = "https://app.shiprocket.in/seller/orders/readytoship"

/** Best-effort clipboard write -- `navigator.clipboard` can throw (denied
 * permission, insecure context, an older browser) and must never break
 * the caller's own flow (opening Shiprocket's page) when it does.
 * Returns whether the copy actually succeeded, so the caller can fall
 * back to showing the id for the operator to copy by hand.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
