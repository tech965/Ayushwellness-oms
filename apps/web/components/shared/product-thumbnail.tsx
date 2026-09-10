"use client"

import * as React from "react"
import { ImageOff } from "lucide-react"

import { cn } from "@/lib/utils"

/** Small square product/variant thumbnail with a clean fallback chain:
 * a given `src` (Shopify CDN URL, rendered directly -- the OMS never
 * re-hosts the image) renders as an `<img>`; a null `src`, OR one that
 * fails to load (deleted from Shopify, network hiccup), renders a
 * neutral placeholder instead -- NEVER a broken-image icon. Fixed
 * square size, rounded corners, `object-cover` so mixed aspect ratios
 * never distort or shift surrounding layout.
 */
export function ProductThumbnail({
  src,
  alt,
  size = "size-10",
  className,
}: {
  src: string | null
  alt: string
  /** Tailwind square-size class, e.g. `"size-10"` (table row) or
   * `"size-14"` (card header). Keep it a fixed size so no image ever
   * shifts the layout while loading.
   */
  size?: string
  className?: string
}) {
  const [failed, setFailed] = React.useState(false)
  // Reset when `src` changes (a resync/mutation refetch feeding a new
  // URL into an already-mounted card) -- otherwise a URL that once
  // failed would stay stuck on the placeholder forever, even once
  // Shopify's image is fixed or replaced. Adjusted during render (React's
  // documented pattern for "reset state when a prop changes") rather
  // than in a `useEffect`, which would setState after an extra commit.
  const [prevSrc, setPrevSrc] = React.useState(src)
  if (src !== prevSrc) {
    setPrevSrc(src)
    setFailed(false)
  }
  const showPlaceholder = !src || failed

  if (showPlaceholder) {
    return (
      <div
        role="img"
        aria-label={alt}
        className={cn(
          "bg-muted text-muted-foreground flex shrink-0 items-center justify-center rounded-md",
          size,
          className
        )}
      >
        <ImageOff className="size-4" aria-hidden="true" />
      </div>
    )
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      onError={() => setFailed(true)}
      className={cn("bg-muted shrink-0 rounded-md object-cover", size, className)}
    />
  )
}
