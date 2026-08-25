"""Direct courier webhook endpoints (Blue Dart, Delhivery, Ecom Express, ...).

Status: PLANNED — foundation added in Phase 2, additional couriers later.

    POST /api/v1/webhooks/couriers/{courier_code}

A single generic endpoint dispatches to the matching
`app.integrations.couriers.CourierAdapter` based on `courier_code`, so
new couriers can be onboarded without adding new routes.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
