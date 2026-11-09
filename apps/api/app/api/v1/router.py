"""Aggregates every V1 route group under app.core.config.settings.API_V1_PREFIX.

Route groups mirror the OMS module boundaries (auth, orders, shipments,
ndr, rto, integrations, ...). Each endpoint module currently exposes an
empty `APIRouter` — see the module docstring for which phase adds real
endpoints. Registering the full namespace now keeps the URL contract
stable as modules are implemented.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1.endpoints import (
    alerts,
    amazon_sales,
    analytics,
    analytics_live,
    audit_logs,
    auth,
    automation,
    cashfree_live,
    cashfree_payments,
    catalog_live,
    chat,
    couriers,
    customers,
    dashboard,
    integrations,
    inventory,
    ndr,
    operations_command_center,
    orders,
    orders_live,
    payments,
    permissions,
    products,
    reconciliation,
    refunds,
    returns,
    roles,
    rto,
    shipment_events,
    shipment_staff,
    shipments,
    shipments_live,
    shiprocket_diagnostics,
    supply_intelligence,
    sync,
    sync_jobs,
    tasks,
    team,
    telecaller,
    users,
    webhook_events,
)
from app.api.v1.endpoints import (
    settings as settings_endpoints,
)
from app.api.v1.webhooks import cashfree as cashfree_webhooks
from app.api.v1.webhooks import couriers as courier_webhooks
from app.api.v1.webhooks import shiprocket as shiprocket_webhooks
from app.api.v1.webhooks import shopify as shopify_webhooks
from app.dependencies.live_mode import unavailable_in_live_mode, unavailable_in_live_mode_for

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(roles.router, prefix="/roles", tags=["roles"])
api_router.include_router(permissions.router, prefix="/permissions", tags=["permissions"])
api_router.include_router(orders.router, prefix="/orders", tags=["orders"])
api_router.include_router(customers.router, prefix="/customers", tags=["customers"])
api_router.include_router(products.router, prefix="/products", tags=["products"])
api_router.include_router(inventory.router, prefix="/inventory", tags=["inventory"])
api_router.include_router(
    cashfree_live.router, prefix="/payments/cashfree/live", tags=["payments:cashfree:live"]
)
_PAYMENTS_LIVE_HINT = "Use the /payments/cashfree/live/* endpoints for live Cashfree data."
api_router.include_router(
    payments.router,
    prefix="/payments",
    tags=["payments"],
    dependencies=[Depends(unavailable_in_live_mode_for(_PAYMENTS_LIVE_HINT))],
)
api_router.include_router(
    cashfree_payments.router,
    prefix="/payments/cashfree",
    tags=["payments:cashfree"],
    dependencies=[Depends(unavailable_in_live_mode_for(_PAYMENTS_LIVE_HINT))],
)
api_router.include_router(shipments.router, prefix="/shipments", tags=["shipments"])
api_router.include_router(
    shipment_events.router, prefix="/shipment-events", tags=["shipment-events"]
)
api_router.include_router(couriers.router, prefix="/couriers", tags=["couriers"])
api_router.include_router(shipments_live.router, prefix="/live", tags=["live"])
api_router.include_router(orders_live.router, prefix="/live/orders", tags=["live:orders"])
api_router.include_router(catalog_live.router, prefix="/live/catalog", tags=["live:catalog"])
api_router.include_router(
    ndr.router,
    prefix="/ndr",
    tags=["ndr"],
    dependencies=[Depends(unavailable_in_live_mode_for("Use GET /live/ndr for live NDR data."))],
)
api_router.include_router(
    rto.router,
    prefix="/rto",
    tags=["rto"],
    dependencies=[
        Depends(
            unavailable_in_live_mode_for(
                "RTO status is shown per shipment (live tracking); a standalone RTO list has "
                "no Shiprocket API in this integration."
            )
        )
    ],
)
api_router.include_router(returns.router, prefix="/returns", tags=["returns"])
api_router.include_router(refunds.router, prefix="/refunds", tags=["refunds"])
api_router.include_router(analytics_live.router, prefix="/analytics/live", tags=["analytics:live"])
api_router.include_router(
    dashboard.router,
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(unavailable_in_live_mode)],
)
api_router.include_router(
    analytics.router,
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(unavailable_in_live_mode)],
)
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(
    supply_intelligence.router,
    prefix="/analytics/supply-intelligence",
    tags=["analytics:supply-intelligence"],
    dependencies=[Depends(unavailable_in_live_mode)],
)
api_router.include_router(
    operations_command_center.router,
    prefix="/analytics/operations-command-center",
    tags=["analytics:operations-command-center"],
    dependencies=[Depends(unavailable_in_live_mode)],
)
api_router.include_router(
    amazon_sales.router, prefix="/marketplaces/amazon/sales", tags=["marketplaces:amazon"]
)
api_router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
api_router.include_router(sync.router, prefix="/sync", tags=["sync"])
api_router.include_router(sync_jobs.router, prefix="/sync-jobs", tags=["sync-jobs"])
api_router.include_router(
    reconciliation.router,
    prefix="/reconciliation",
    tags=["reconciliation"],
    dependencies=[
        Depends(
            unavailable_in_live_mode_for(
                "Reconciliation compares stored records with the providers; use the live "
                "payment verification (/payments/cashfree/live/orders/{id}) instead."
            )
        )
    ],
)
api_router.include_router(webhook_events.router, prefix="/webhook-events", tags=["webhook-events"])
api_router.include_router(automation.router, prefix="/automation", tags=["automation"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
api_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_router.include_router(audit_logs.router, prefix="/audit-logs", tags=["audit-logs"])
api_router.include_router(settings_endpoints.router, prefix="/settings", tags=["settings"])
api_router.include_router(team.router, prefix="/team", tags=["team"])
api_router.include_router(telecaller.router, prefix="/telecaller", tags=["telecaller"])
api_router.include_router(shipment_staff.router, prefix="/shipment-staff", tags=["shipment-staff"])

# TEMPORARY -- see app/api/v1/endpoints/shiprocket_diagnostics.py's module
# docstring for exactly what this is, why it's safe, and how to remove it
# once the #AWL95498 investigation is closed.
api_router.include_router(
    shiprocket_diagnostics.router,
    prefix="/diagnostics/shiprocket",
    tags=["diagnostics:shiprocket (temporary)"],
)

api_router.include_router(
    shopify_webhooks.router, prefix="/webhooks/shopify", tags=["webhooks:shopify"]
)
api_router.include_router(
    shiprocket_webhooks.router, prefix="/webhooks/shiprocket", tags=["webhooks:shiprocket"]
)
# Same router, same handler, mounted a second time under a URL that
# doesn't contain "shiprocket" -- confirmed live: Shiprocket's own
# "Webhooks" dashboard page refuses the URL above with "Please refrain
# from using keywords like shiprocket, kartrocket, sr, or kr in the
# webhook url." The `/webhooks/shiprocket/...` path stays registered
# (nothing depends on removing it, and any docs/tests referencing it
# keep working) -- this is purely an additive alias to actually enter
# into Shiprocket's dashboard.
api_router.include_router(
    shiprocket_webhooks.router,
    prefix="/webhooks/shipment-updates",
    tags=["webhooks:shiprocket"],
)
api_router.include_router(
    courier_webhooks.router, prefix="/webhooks/couriers", tags=["webhooks:couriers"]
)
api_router.include_router(
    cashfree_webhooks.router, prefix="/webhooks/cashfree", tags=["webhooks:cashfree"]
)
