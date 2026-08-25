import Link from "next/link"

import { ScrollArea } from "@/components/ui/scroll-area"
import { SidebarNav } from "@/components/layout/sidebar-nav"

export function AppSidebar() {
  return (
    <aside className="border-sidebar-border bg-sidebar hidden w-64 shrink-0 border-r md:flex md:flex-col">
      <div className="border-sidebar-border flex h-14 items-center gap-2 border-b px-4">
        <Link href="/dashboard" className="flex items-center gap-2 font-semibold">
          <span className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-md text-sm">
            A
          </span>
          <span className="text-sidebar-foreground text-sm">AyushWellness OMS</span>
        </Link>
      </div>
      <ScrollArea className="flex-1">
        <SidebarNav />
      </ScrollArea>
    </aside>
  )
}
