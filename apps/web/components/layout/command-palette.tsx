"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { Search } from "lucide-react"

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { allNavItems } from "@/lib/navigation"

/** Lightweight Cmd/Ctrl+K navigation palette — plain Dialog + Input +
 * filtered list rather than pulling in `cmdk`, since jumping between the
 * ~20 known nav destinations doesn't need a dedicated headless library.
 */
export function CommandPalette() {
  const router = useRouter()
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState("")

  React.useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault()
        setOpen((value) => {
          const next = !value
          if (!next) setQuery("")
          return next
        })
      }
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  const matches = allNavItems.filter((item) =>
    item.label.toLowerCase().includes(query.toLowerCase())
  )

  function handleOpenChange(next: boolean) {
    setOpen(next)
    // Reset directly in the same handler that closes the dialog, rather
    // than reactively in an effect keyed on `open` — no extra render pass.
    if (!next) setQuery("")
  }

  function go(href: string) {
    handleOpenChange(false)
    router.push(href)
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="border-input bg-background text-muted-foreground hover:bg-accent hidden items-center gap-2 rounded-md border px-3 py-1.5 text-sm sm:flex"
      >
        <Search className="size-4" />
        <span>Search pages...</span>
        <kbd className="bg-muted ml-4 rounded px-1.5 py-0.5 font-mono text-[10px]">
          ⌘K
        </kbd>
      </button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent
          className="gap-0 overflow-hidden p-0 sm:max-w-md"
          showCloseButton={false}
        >
          <DialogTitle className="sr-only">Search pages</DialogTitle>
          <div className="flex items-center gap-2 border-b px-3">
            <Search className="text-muted-foreground size-4 shrink-0" />
            <Input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Jump to a page..."
              className="border-0 shadow-none focus-visible:ring-0"
            />
          </div>
          <div className="max-h-80 overflow-y-auto p-1">
            {matches.length === 0 && (
              <p className="text-muted-foreground p-4 text-center text-sm">No matches.</p>
            )}
            {matches.map((item) => (
              <button
                key={item.href}
                type="button"
                onClick={() => go(item.href)}
                className="hover:bg-accent flex w-full items-center gap-2.5 rounded-sm px-3 py-2 text-left text-sm"
              >
                <item.icon className="text-muted-foreground size-4" />
                {item.label}
              </button>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
