"use client"

import * as React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { LogOut, Menu, ShieldAlert } from "lucide-react"

import { ThemeToggle } from "@/components/layout/theme-toggle"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { useAuth } from "@/lib/auth-context"
import { imsNav } from "@/lib/ims-nav"
import { cn } from "@/lib/utils"

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname()
  const { hasPermission } = useAuth()
  return (
    <nav className="flex flex-col gap-0.5 px-3 py-4">
      {imsNav
        .filter((i) => !i.permission || hasPermission(i.permission))
        .map((item) => {
          const active =
            item.href === "/ims" ? pathname === "/ims" : pathname === item.href || pathname.startsWith(`${item.href}/`)
          const Icon = item.icon
          return (
            <Link
              key={item.href}
              href={item.href}
              prefetch={false}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground font-semibold"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
              )}
            >
              <Icon className="size-4 shrink-0" />
              {item.label}
            </Link>
          )
        })}
    </nav>
  )
}

export function ImsShell({ children }: { children: React.ReactNode }) {
  const { user, isLoading, hasPermission, logout } = useAuth()
  const canView = hasPermission("ims.read")

  return (
    <div className="bg-muted/30 flex h-dvh overflow-hidden">
      <aside className="border-sidebar-border bg-sidebar hidden w-60 shrink-0 flex-col border-r md:flex">
        <div className="border-sidebar-border flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <span className="bg-primary text-primary-foreground flex size-7 items-center justify-center rounded-lg text-sm font-bold">
            I
          </span>
          <div className="leading-tight">
            <div className="text-sm font-semibold">Inventory</div>
            <div className="text-muted-foreground text-[0.6875rem]">AyushWellness IMS</div>
          </div>
        </div>
        <ScrollArea className="min-h-0 flex-1">{isLoading ? <Skeleton className="m-4 h-40" /> : <Nav />}</ScrollArea>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-border bg-header flex h-14 shrink-0 items-center justify-between gap-3 border-b px-4">
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="md:hidden">
                <Menu className="size-5" />
                <span className="sr-only">Open navigation</span>
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-64 p-0">
              <SheetTitle className="sr-only">Inventory navigation</SheetTitle>
              <div className="border-border flex h-14 items-center border-b px-4 font-semibold">Inventory</div>
              <Nav />
            </SheetContent>
          </Sheet>
          <div className="flex-1" />
          <div className="flex items-center gap-2">
            {hasPermission("orders.read") && (
              <Button asChild variant="ghost" size="sm">
                <Link href="/dashboard">Back to OMS</Link>
              </Button>
            )}
            <ThemeToggle />
            <span className="hidden text-sm font-medium sm:inline">{user?.name}</span>
            <Button variant="ghost" size="icon" onClick={logout} aria-label="Log out">
              <LogOut className="size-4" />
            </Button>
          </div>
        </header>
        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          {isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : canView ? (
            children
          ) : (
            <div className="mx-auto mt-16 flex max-w-md flex-col items-center gap-2 text-center">
              <ShieldAlert className="text-muted-foreground size-8" />
              <h1 className="text-lg font-semibold">No access to Inventory</h1>
              <p className="text-muted-foreground text-sm">
                Your account does not have the inventory permission. Ask an administrator to assign the
                Inventory Staff role.
              </p>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
