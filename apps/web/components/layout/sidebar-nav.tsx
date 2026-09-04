"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useAuth } from "@/lib/auth-context"
import { cn } from "@/lib/utils"
import { navGroups, teamLeaderNavGroups, telecallerNavGroups } from "@/lib/navigation"

export function SidebarNav({ collapsed = false }: { collapsed?: boolean }) {
  const pathname = usePathname()
  const { hasRole, hasPermission } = useAuth()

  // A Telecaller/Team Leader gets the minimal role-specific nav instead
  // of the full Admin OMS menu (spec: "Do not show the full Admin OMS
  // navigation") — purely a UI simplification; the actual access
  // boundary is enforced server-side regardless of which links render
  // here. Superusers/other roles keep the full `navGroups`.
  const baseGroups = hasRole("TELECALLER")
    ? telecallerNavGroups
    : hasRole("TEAM_LEADER")
      ? teamLeaderNavGroups
      : navGroups

  // RBAC nav gating: "Users" and "Roles" only render for a viewer who
  // actually has `users.manage`/`roles.manage`. An item with no
  // `permission` (Settings, plus every non-Administration item) always
  // passes regardless of those two permissions — Settings deliberately
  // stays visible even when both are absent, since it isn't gated by
  // either. A group is only dropped entirely once filtering leaves it
  // with zero items; "Administration" never hits that today because
  // Settings always survives the filter. This is UX gating only, same
  // caveat as `RoleRedirect`: every underlying admin API call is
  // independently permission-checked server-side regardless of what
  // renders here.
  const effectiveGroups = baseGroups
    .map((group) => ({
      ...group,
      items: group.items.filter(
        (item) => !item.permission || hasPermission(item.permission)
      ),
    }))
    .filter((group) => group.items.length > 0)

  return (
    <nav className="flex flex-col gap-1 px-3 py-4">
      {effectiveGroups.map((group, index) => (
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
                // Pre-demo fix: this list renders every one of ~31 nav
                // destinations simultaneously, all visible in the
                // persistent sidebar on every dashboard page. Next.js's
                // default Link behavior auto-prefetches each one as it
                // enters the viewport, which at this volume produced
                // Chrome's own "Throttling navigation to prevent the
                // browser from hanging" warning and the excessive
                // `?_rsc=` request volume behind the visible navigation
                // blinking. Disabling automatic prefetch here only stops
                // that proactive background fetching -- clicking a link
                // still navigates exactly as before (Next.js fetches the
                // RSC payload for the clicked destination on click,
                // same as any non-prefetched Link).
                prefetch={false}
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
