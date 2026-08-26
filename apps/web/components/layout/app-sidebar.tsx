"use client"

import Link from "next/link"
import { PanelLeftClose, PanelLeftOpen } from "lucide-react"

import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { SidebarNav } from "@/components/layout/sidebar-nav"
import { useLocalStorageState } from "@/lib/use-local-storage-state"
import { useMounted } from "@/lib/use-mounted"
import { cn } from "@/lib/utils"

const STORAGE_KEY = "oms_sidebar_collapsed"

export function AppSidebar() {
  const [collapsed, setCollapsed] = useLocalStorageState(
    STORAGE_KEY,
    false,
    (v) => (v ? "1" : "0"),
    (raw) => raw === "1"
  )
  const hydrated = useMounted()

  function toggle() {
    setCollapsed(!collapsed)
  }

  return (
    <aside
      className={cn(
        "border-sidebar-border bg-sidebar hidden shrink-0 flex-col border-r transition-[width] duration-200 md:flex",
        // Suppress the collapse transition on first paint so the sidebar
        // doesn't visibly animate from expanded -> collapsed on every
        // hard refresh while the stored preference is being read.
        !hydrated && "duration-0",
        collapsed ? "w-16" : "w-64"
      )}
    >
      <div className="border-sidebar-border flex h-14 items-center gap-2 border-b px-4">
        <Link href="/dashboard" className="flex items-center gap-2 font-semibold">
          <span className="bg-primary text-primary-foreground flex size-7 shrink-0 items-center justify-center rounded-md text-sm">
            A
          </span>
          {!collapsed && (
            <span className="text-sidebar-foreground text-sm">AyushWellness OMS</span>
          )}
        </Link>
      </div>
      <ScrollArea className="flex-1">
        <SidebarNav collapsed={collapsed} />
      </ScrollArea>
      <div className="border-sidebar-border border-t p-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={toggle}
          className="text-sidebar-foreground/70 hover:text-sidebar-foreground w-full justify-center"
        >
          {collapsed ? (
            <PanelLeftOpen className="size-4" />
          ) : (
            <>
              <PanelLeftClose className="size-4" />
              Collapse
            </>
          )}
        </Button>
      </div>
    </aside>
  )
}
