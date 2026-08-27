"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { navGroups } from "@/lib/navigation"

export function SidebarNav({ collapsed = false }: { collapsed?: boolean }) {
  const pathname = usePathname()

  return (
    <nav className="flex flex-col gap-1 px-3 py-4">
      {navGroups.map((group, index) => (
        <div key={group.label} className="flex flex-col gap-1">
          {index > 0 && <Separator className="my-2" />}
          {!collapsed && (
            <span className="text-muted-foreground px-3 pb-0.5 text-[0.6875rem] font-semibold tracking-widest uppercase">
              {group.label}
            </span>
          )}
          {group.items.map((item) => {
            const isActive =
              pathname === item.href || pathname.startsWith(`${item.href}/`)
            const Icon = item.icon

            const link = (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "relative flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  collapsed && "justify-center px-0",
                  isActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground font-semibold"
                    : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                )}
              >
                {isActive && (
                  <span className="bg-sidebar-primary absolute top-1/2 left-0 h-4 w-0.5 -translate-y-1/2 rounded-r-full" />
                )}
                <Icon className="size-4 shrink-0" />
                {!collapsed && item.label}
              </Link>
            )

            if (!collapsed) return link

            return (
              <Tooltip key={item.href}>
                <TooltipTrigger asChild>{link}</TooltipTrigger>
                <TooltipContent side="right">{item.label}</TooltipContent>
              </Tooltip>
            )
          })}
        </div>
      ))}
    </nav>
  )
}
