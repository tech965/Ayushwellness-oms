import type { ReactElement } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, type RenderResult } from "@testing-library/react"

import { TooltipProvider } from "@/components/ui/tooltip"

/** Wraps a component under test in a fresh QueryClientProvider (every
 * retry/staleTime disabled so tests are deterministic and fast) plus the
 * same `TooltipProvider` `app/providers.tsx` mounts in production — any
 * component under test that renders a `Tooltip` needs one in its
 * ancestry or Radix throws, same as it would in the real app tree.
 */
export function renderWithProviders(ui: ReactElement): RenderResult {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0 },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>
  )
}
