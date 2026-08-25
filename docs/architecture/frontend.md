# Frontend Architecture

Next.js 16 (App Router), TypeScript strict mode, Tailwind CSS v4,
shadcn/ui, TanStack Query, React Hook Form + Zod, Recharts.

## Layout

```
apps/web/
├── app/
│   ├── layout.tsx              # root layout: fonts, Providers, Toaster
│   ├── page.tsx                 # redirects "/" → "/dashboard"
│   ├── providers.tsx             # QueryClientProvider, TooltipProvider
│   ├── login/page.tsx            # real login form, wired to the API
│   └── (dashboard)/               # route group — wraps every internal page
│       ├── layout.tsx             # <AuthProvider><AppShell>...
│       ├── dashboard/page.tsx     # live stat counts (Phase 1)
│       ├── orders/page.tsx, orders/[id]/page.tsx           # live (Phase 1)
│       ├── customers/page.tsx, customers/[id]/page.tsx     # live (Phase 1)
│       ├── products/page.tsx                                # live, list-only (Phase 1)
│       ├── shipments/page.tsx, shipments/[id]/page.tsx     # live (Phase 1)
│       ├── ndr/page.tsx, rto/page.tsx                       # live (Phase 1)
│       ├── audit-logs/page.tsx                               # live (Phase 1)
│       └── returns/, refunds/, couriers/, analytics/, integrations/,
│           automation/, alerts/, tasks/, users/, roles/, settings/  # still PhasePlaceholder
├── components/
│   ├── ui/                      # shadcn/ui primitives (generated)
│   ├── layout/                  # AppShell, AppSidebar, Topbar, SidebarNav
│   └── shared/                  # PageHeader, PhasePlaceholder, DataTable,
│                                 #   FilterBar, PaginationBar, QueryStates, StatusBadge
├── lib/                          # api-client, auth-context, query-client, navigation,
│                                 #   status-styles, format, use-pagination, utils, validation/
├── services/                     # one file per backend module — TanStack Query hooks
├── types/                        # API contract types mirroring the backend schemas
├── test-utils/                   # renderWithProviders (QueryClientProvider wrapper)
└── vitest.config.ts / vitest.setup.ts
```

## Route groups, not a fake "no layout" trick

`app/(dashboard)/layout.tsx` wraps every operational page in
`<AuthProvider><AppShell>...` (auth guard, then sidebar navigation +
topbar). `app/login/page.tsx` sits outside that group so the login
screen renders without the authenticated chrome. This is the standard
Next.js route-group pattern — the `(dashboard)` segment does not appear
in the URL.

## Authentication (no middleware — see `docs/api/authentication.md`)

Tokens live in `localStorage` (`lib/api-client.ts`), not a cookie, so
there's nothing for Next.js Edge middleware to read. Route protection is
`lib/auth-context.tsx`'s `AuthProvider` instead: it calls `GET /auth/me`
on mount and redirects to `/login` if there's no token or the call
fails. `useAuth()` exposes `user`, `permissions`, `hasPermission(code)`,
and `logout()` to any component inside `(dashboard)` — used to
conditionally show mutating controls (e.g. the order status-transition
picker only renders for a user with `orders.update`/`orders.cancel`).
Hiding a control this way is UX only; the backend is still the real
authorization boundary (`docs/security/rbac.md`).

`lib/api-client.ts`'s response interceptor retries once through
`POST /auth/refresh` on a `401` (coalescing concurrent 401s into a
single refresh call) before giving up and hard-redirecting to `/login`.

## Data fetching

- **Server state** (anything from the API): TanStack Query, via
  `lib/query-client.ts` (`createQueryClient`) and a per-feature
  `services/*.ts` module that wraps `lib/api-client.ts`. Each module
  exports plain `useXxx`/`useXxxList` query hooks and, where the backend
  supports a mutation, a `useCreateXxx`/`useUpdateXxx` hook that
  invalidates the relevant query keys on success.
- **List pages** all follow the same shape: local `useState` for
  search/status/date-range filters + `usePaginationState()`
  (`lib/use-pagination.ts`, resets to page 1 whenever a filter changes)
  → a `services/*.ts` list hook → `<FilterBar>` +
  `<QueryStates>` (loading skeleton / error+retry / empty state — spec
  §48: no page renders blank) wrapping `<DataTable>` + `<PaginationBar>`.
  This shared shape is what keeps pagination/filter/empty-state logic
  from being reimplemented per page (`components/shared/`).
- **Forms**: React Hook Form + Zod resolver, with `components/ui/form.tsx`
  (the standard shadcn Form/FormField/FormItem/FormControl pattern) — see
  `app/login/page.tsx` for the reference implementation.
- **Client state**: local component state / URL search params. No global
  client state library is introduced until a concrete need appears.

## The `ApiResponse<T>` contract

`types/api.ts` mirrors the backend's response envelope
(`apps/api/app/schemas/response.py`) exactly:

```ts
interface ApiResponse<T> {
  success: boolean
  data: T | null
  message: string
  meta: Record<string, unknown>
}
```

Every `services/*.ts` function returns the *unwrapped* `data`, not the
envelope — callers (components, TanStack Query hooks) never see `success`/
`message` directly. See `services/auth.ts`.

## Shared list/detail building blocks (`components/shared/`)

- **`DataTable`** — a plain column-definition table (`{id, header, cell}`),
  deliberately *not* built on a headless table library: sorting and
  pagination are server-driven (query params against the backend), so
  there's no client-side row model to manage.
- **`FilterBar`** — composable search input + status `Select` + date-range
  (`Popover` + `Calendar`) row; any list page opts into only the pieces it
  needs via props.
- **`PaginationBar`** — reads/writes against the backend's
  `PaginationMeta`.
- **`QueryStates`** — the loading/error/empty wrapper described above.
- **`StatusBadge`** + `lib/status-styles.ts` — maps each backend status
  enum (order/payment/shipment/shipment_delay/ndr/rto/return/refund/product)
  to a consistent color tone, in one place instead of per-page ad hoc
  Tailwind classes.

## Module pages before their phase lands

Pages not yet listed under Phase 1 in the layout tree above still render
`<PhasePlaceholder>` (`components/shared/phase-placeholder.tsx`) — a
labeled empty state naming the module and the phase that implements it.
This keeps the route/URL contract and navigation fixed without shipping
fake data. Real content replaces the placeholder module-by-module as
each phase lands (see `docs/roadmap.md`).

## Design system

Tailwind v4 CSS-first config lives in `app/globals.css` (no
`tailwind.config.js`). shadcn/ui is configured via `components.json`
(`style: "radix-nova"`, neutral base color) — a deliberately restrained,
data-dense palette appropriate for an internal operations tool, not a
consumer product. Icons are `lucide-react`; charts (Phase 3+) use
`recharts`.

## Testing

Vitest + React Testing Library (`vitest.config.ts`, jsdom environment).
Component tests live next to the component in a `__tests__/` folder;
schema/logic tests live next to the module they test (e.g.
`lib/validation/__tests__/auth.test.ts`, `lib/__tests__/status-styles.test.ts`).
Page-level tests mock the relevant `services/*.ts` hook and `next/navigation`,
then render through `test-utils/render-with-providers.tsx`
(`renderWithProviders`) to get a real `QueryClientProvider` without a real
network call — see `app/(dashboard)/orders/__tests__/page.test.tsx`.
