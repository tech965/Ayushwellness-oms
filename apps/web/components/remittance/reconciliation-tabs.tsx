"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { cn } from "@/lib/utils"

const TABS = [
  { href: "/reconciliation/cod", label: "COD Remittance Reconciliation" },
  { href: "/reconciliation/prepaid", label: "Prepaid Remittance Reconciliation" },
  { href: "/reconciliation", label: "Data Sync Checks", exact: true },
]

/** Sub-navigation shared by the three pages that live under the existing
 * "Reconciliation" sidebar entry. Shiprocket Wallet is a separate page and is
 * deliberately not part of this strip.
 */
export function ReconciliationTabs() {
  const pathname = usePathname()
  return (
    <nav
      aria-label="Reconciliation sections"
      className="flex flex-wrap gap-1 border-b pb-2"
    >
      {TABS.map((tab) => {
        const active = tab.exact ? pathname === tab.href : pathname.startsWith(tab.href)
        return (
          <Link
            key={tab.href}
            href={tab.href}
            prefetch={false}
            aria-current={active ? "page" : undefined}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
              active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-foreground"
            )}
          >
            {tab.label}
          </Link>
        )
      })}
    </nav>
  )
}
