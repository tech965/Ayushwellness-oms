"use client"

import * as React from "react"
import { Eye, EyeOff } from "lucide-react"

import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

/**
 * `Input` with a show/hide toggle -- masked (`type="password"`) by
 * default, reveals as plain text while the eye icon is toggled on.
 * Drop-in replacement for `<Input type="password" ... />`: forwards
 * every prop `Input` does (including react-hook-form's `{...field}`
 * spread), so existing validation/form wiring is untouched. The
 * revealed/hidden state is local UI state only -- the value itself is
 * never logged or persisted anywhere by this component.
 */
function PasswordInput({ className, ...props }: React.ComponentProps<"input">) {
  const [visible, setVisible] = React.useState(false)

  return (
    <div className="relative">
      <Input type={visible ? "text" : "password"} className={cn("pr-9", className)} {...props} />
      <button
        type="button"
        onClick={() => setVisible((current) => !current)}
        aria-label={visible ? "Hide password" : "Show password"}
        aria-pressed={visible}
        className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 absolute inset-y-0 right-0 flex items-center rounded-r-lg px-2.5 outline-none focus-visible:ring-3"
      >
        {visible ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />}
      </button>
    </div>
  )
}

export { PasswordInput }
