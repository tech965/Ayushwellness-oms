"use client"

import * as React from "react"
import { ChevronDown, ChevronRight } from "lucide-react"

import { fmt } from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useCategoriesOverview } from "@/services/ims"
import type { CategoryOverviewEntry, CategoryOverviewGroup, NamedLocation } from "@/types/ims"

/** "Warehouse 1" + code "251" -> "Warehouse 251"; "Office" + "402" -> "Office 402".
 * The label/grouping logic lives on the backend; this only formats it for display. */
function locationHeading(loc: NamedLocation): string {
  return loc.label === "Office" ? `Office ${loc.code}` : `Warehouse ${loc.code}`
}

function GroupRow({ group, locations }: { group: CategoryOverviewGroup; locations: NamedLocation[] }) {
  const [open, setOpen] = React.useState(false)
  return (
    <div className="border-t">
      <button
        type="button"
        className="hover:bg-muted/50 flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="flex min-w-0 items-center gap-2 font-medium">
          {open ? <ChevronDown className="text-muted-foreground size-3.5 shrink-0" /> : <ChevronRight className="text-muted-foreground size-3.5 shrink-0" />}
          <span className="truncate">{group.name}</span>
        </span>
        <span className="text-muted-foreground shrink-0 tabular-nums">{fmt(group.total)} total stock</span>
      </button>
      {open && (
        <div className="overflow-x-auto px-4 pb-3">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>SKU</TableHead>
                <TableHead>Packaging</TableHead>
                {locations.map((l) => (
                  <TableHead key={l.id} className="text-right">
                    {locationHeading(l)}
                  </TableHead>
                ))}
                <TableHead className="text-right">Total Stock</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {group.skus.map((s) => (
                <TableRow key={s.sku}>
                  <TableCell className="font-mono text-xs">
                    {s.sku}
                    {!s.found_in_catalog && (
                      <Badge variant="destructive" className="ml-2 text-[0.65rem]">
                        not in Shopify
                      </Badge>
                    )}
                    {s.status && s.status !== "active" && (
                      <Badge variant="outline" className="ml-2 text-[0.65rem]">
                        {s.status}
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">{s.packaging}</TableCell>
                  {locations.map((l) => (
                    <TableCell key={l.id} className="text-right tabular-nums">
                      {fmt(s.by_warehouse[l.id] ?? 0)}
                    </TableCell>
                  ))}
                  <TableCell className="text-right font-semibold tabular-nums">{fmt(s.total)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}

function CategorySection({ entry, locations }: { entry: CategoryOverviewEntry; locations: NamedLocation[] }) {
  const [open, setOpen] = React.useState(false)
  return (
    <div className="bg-card overflow-hidden rounded-lg border">
      <button
        type="button"
        className="hover:bg-muted/40 flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="flex items-center gap-2 font-semibold">
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          {entry.category}
        </span>
        <span className="text-muted-foreground flex items-center gap-5 text-sm">
          <span>
            <span className="tabular-nums">{fmt(entry.total_products)}</span> products ·{" "}
            <span className="tabular-nums">{fmt(entry.total_skus)}</span> SKUs
          </span>
          <span className="text-foreground font-semibold tabular-nums">{fmt(entry.total_stock)} total stock</span>
        </span>
      </button>
      {open && (
        <div>
          {entry.groups.map((g) => (
            <GroupRow key={g.name} group={g} locations={locations} />
          ))}
          <div className="bg-muted/30 flex items-center justify-between border-t px-4 py-2 text-sm font-semibold">
            <span>{entry.category} total</span>
            <span className="tabular-nums">{fmt(entry.total_stock)}</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function ImsCategoriesPage() {
  const query = useCategoriesOverview()
  return (
    <div className="space-y-4">
      <PageHeader
        title="Categories"
        description="Herbal Masala, Ayurvedic Products and Nutraceutical Products — click a category, then a product or flavour, to see its SKUs and stock by location."
      />
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => query.refetch()}
        isEmpty={(d) => d.items.length === 0}
      >
        {(d) => (
          <div className="space-y-3">
            {d.items.map((entry) => (
              <CategorySection key={entry.category} entry={entry} locations={d.locations} />
            ))}
            {d.unclassified.length > 0 && (
              <div className="bg-muted/20 rounded-lg border border-dashed p-4">
                <p className="text-sm font-medium">Not in Herbal Masala, Ayurvedic or Nutraceutical</p>
                <p className="text-muted-foreground text-xs">
                  Existing classification preserved — confirm where these belong before adding them to a category.
                </p>
                <ul className="mt-2 space-y-1 text-sm">
                  {d.unclassified.map((u) => (
                    <li key={u.sku} className="flex items-center justify-between gap-2">
                      <span>
                        {u.title} <span className="text-muted-foreground font-mono text-xs">{u.sku}</span>
                      </span>
                      <span className="text-muted-foreground text-xs">{u.current_category ?? "not found in catalogue"}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </QueryStates>
    </div>
  )
}
