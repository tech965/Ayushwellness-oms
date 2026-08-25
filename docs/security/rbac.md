# RBAC (Role-Based Access Control)

Status: **IMPLEMENTED** (Phase 1). `Role`, `Permission`, `UserRole`,
`RolePermission` (`app/models/rbac.py`) and the
`require_permission(...)` FastAPI dependency
(`app/dependencies/auth.py`) are live.

## Permission naming

Permissions are `module.action` strings (e.g. `orders.read`,
`orders.create`, `orders.cancel`, `users.manage`) — **not** the
`module:action` two-verb (`read`/`write`) scheme this document sketched
during Phase 0 planning. `orders` in particular has four distinct
actions (`read`, `create`, `update`, `cancel`) rather than one
`write` — `orders.cancel` is checked separately from `orders.update` so
a role like `CUSTOMER_SUPPORT` can cancel an order without also gaining
every other order-lifecycle transition.

Full list, seeded by `apps/api/scripts/seed.py`:

`orders.read`, `orders.create`, `orders.update`, `orders.cancel`,
`shipments.read`, `shipments.update`, `customers.read`,
`customers.update`, `products.read`, `products.update`, `ndr.read`,
`ndr.update`, `rto.read`, `rto.update`, `returns.read`,
`returns.update`, `refunds.read`, `payments.read`, `couriers.read`,
`couriers.update`, `analytics.read`, `users.manage`, `roles.manage`,
`audit_logs.read`.

## Roles

| Role | Access |
|---|---|
| `ADMIN` | Everything (all permissions, plus every seeded admin user has `is_superuser=True`, which bypasses permission checks entirely) |
| `OPERATIONS` | Orders (read/update/cancel), Shipments, NDR, RTO, Couriers, Customers (read) |
| `CUSTOMER_SUPPORT` | Customers, Orders (read/cancel), Shipments (read), NDR (read), Returns |
| `MARKETING` | Analytics (read) — Meta/Instagram/Leads/Campaigns access is added once those modules ship (Phase 5/6) |
| `MANAGEMENT` | Analytics, and read access across Orders/Customers/Shipments/NDR/RTO/Returns/Refunds/Payments/Couriers |

A user can hold more than one role (`UserRole` is a join table);
effective permissions are the union across all assigned roles
(`User.permission_codes` in `app/models/auth.py`).

## Enforcement is backend-first

The frontend hides UI a user can't use (nav items, action buttons — see
`useAuth().hasPermission()` in `apps/web/lib/auth-context.tsx`), but
that is UX only. Every mutating (and most reading) API endpoint checks
authorization server-side via `Depends(require_permission("orders.read"))`
(`app/dependencies/auth.py`). Hiding a button on the frontend is never
treated as an access control boundary.

## Where this plugs into the request pipeline

```
JWT auth dependency (who is this?) — get_current_user
        ↓
require_permission(...) dependency (can they do this?)
        ↓
Route handler → Service → Repository
```

See `docs/api/authentication.md` for the authentication half of this
pipeline.
