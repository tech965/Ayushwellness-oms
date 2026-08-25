# Audit Logging

Status: PLANNED (Phase 1). The `AuditLog` model (see
`docs/database/schema.md#audit`) and the service that writes to it don't
exist yet; this document is the contract for what must be logged once
they do.

## What must create an AuditLog row

Every manual (human-initiated) operation that changes state, including
but not limited to:

- Changing an order's courier, status, or cancellation state
- Manually creating/editing/deleting a Customer, Product, User, Role
- Changing a Role's permissions
- Manually marking an NDR/RTO outcome
- Approving a Return/Refund
- Editing an AutomationRule
- Any admin override of an integration sync

Automated system actions (a Celery task updating `Shipment.current_status`
from a Shiprocket webhook) are captured in `ShipmentEvent`/`OrderEvent`
instead — `AuditLog` is specifically for **human-initiated** changes,
which is why it also records `previous_value` / `new_value` for diffing.

## What each entry contains

| Field | Meaning |
|---|---|
| `user_id` | who made the change |
| `action` | e.g. `order.courier_changed` |
| `entity_type` / `entity_id` | what was changed |
| `previous_value` / `new_value` | JSON diff |
| `ip_address` | if available from the request |
| `metadata` | anything else relevant (e.g. reason text) |
| `created_at` | when |

Example (from the source specification):

```
User: Vishwa
Action: Changed courier
Order: AW10452
From: Delhivery
To: Blue Dart
```

## Read access

`GET /api/v1/audit-logs` (Phase 1) is read-only and itself subject to
RBAC (`audit_logs:read` — typically `ADMIN` and `MANAGEMENT` only). Audit
log rows are never editable or deletable through the API.
