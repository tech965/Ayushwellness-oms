"""Cashfree Payment Gateway integration.

Unlike Shopify/Shiprocket, Cashfree is not a pull-sync provider — there
is no "list every payment" feed the generic `SyncService`/
`IntegrationAdapter` pull loop fits. It's a request/response payment API
(create/get order, get payment) plus an inbound webhook, so this package
deliberately does not register an `IntegrationAdapter`: nothing here
needs `fetch`/`fetch_incremental`. See `app.services.
cashfree_payment_service.CashfreePaymentService`, which is explicitly
Cashfree-aware the same way `ShiprocketOperationsService` is explicitly
Shiprocket-aware.

An `Integration` row with `code="cashfree"` still exists (seeded by
`scripts/seed.py`) purely so the generic `WebhookService.ingest()` has an
`integration_id` to attach webhook deliveries to, exactly like Shopify's
and Shiprocket's rows.
"""

from __future__ import annotations
