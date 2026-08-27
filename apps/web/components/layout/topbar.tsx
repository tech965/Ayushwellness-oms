"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Menu, LogOut, Settings, UserRound } from "lucide-react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { CommandPalette } from "@/components/layout/command-palette"
import { SidebarNav } from "@/components/layout/sidebar-nav"
import { ThemeToggle } from "@/components/layout/theme-toggle"
import { useAuth } from "@/lib/auth-context"
import { allNavItems } from "@/lib/navigation"

function initials(name: string): string {
  const parts = name.trim().split(/\s+/)
  const chars =
    parts.length > 1 ? [parts[0][0], parts[parts.length - 1][0]] : [parts[0]?.[0] ?? "?"]
  return chars.join("").toUpperCase()
}

function useCurrentSectionLabel(): string {
  const pathname = usePathname()
  const match = allNavItems
    .filter((item) => pathname === item.href || pathname.startsWith(`${item.href}/`))
    .sort((a, b) => b.href.length - a.href.length)[0]
  return match?.label ?? "AyushWellness OMS"
}

export function Topbar() {
  const { user, logout } = useAuth()
  const displayName = user?.name ?? "..."
  const sectionLabel = useCurrentSectionLabel()

  return (
    <header className="border-border bg-header flex h-14 shrink-0 items-center justify-between gap-3 border-b px-4">
      <div className="flex min-w-0 items-center gap-2">
        <Sheet>
          <SheetTrigger asChild>
            <Button variant="ghost" size="icon" className="md:hidden">
              <Menu className="size-5" />
              <span className="sr-only">Open navigation</span>
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="w-64 p-0">
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <div className="border-border flex h-14 items-center border-b px-4 font-semibold">
              AyushWellness OMS
            </div>
            <SidebarNav />
          </SheetContent>
        </Sheet>
        <h2 className="text-foreground truncate text-sm font-semibold">{sectionLabel}</h2>
      </div>

      <div className="flex items-center gap-2">
        <CommandPalette />
        <ThemeToggle />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" className="gap-2 px-2">
              <Avatar className="ring-border ring-offset-background size-7 ring-1 ring-offset-1">
                <AvatarFallback className="bg-secondary text-secondary-foreground text-xs font-semibold">
                  {initials(displayName)}
                </AvatarFallback>
              </Avatar>
              <span className="hidden text-left sm:flex sm:flex-col sm:leading-tight">
                <span className="text-sm font-medium">{displayName}</span>
                {user?.roles?.[0] && (
                  <span className="text-muted-foreground text-[0.6875rem] capitalize">
                    {user.roles[0]}
                  </span>
                )}
              </span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="flex flex-col gap-0.5">
              <span className="text-sm font-medium">{displayName}</span>
              {user?.email && (
                <span className="text-muted-foreground text-xs font-normal">
                  {user.email}
                </span>
              )}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/settings">
                <UserRound className="size-4" />
                Profile
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/settings">
                <Settings className="size-4" />
                Settings
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={logout}>
              <LogOut className="size-4" />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  )
}
