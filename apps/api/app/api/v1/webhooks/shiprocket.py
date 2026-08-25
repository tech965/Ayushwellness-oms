"""Shiprocket webhook endpoints.

Status: PLANNED — implemented in Phase 2.

    POST /api/v1/webhooks/shiprocket/tracking
    POST /api/v1/webhooks/shiprocket/ndr

Handlers will verify the Shiprocket webhook token via
`app.integrations.shiprocket.ShiprocketWebhookHandler` and create a new
ShipmentEvent per update (history is never overwritten), deduplicated by
external event ID through the WebhookEvent idempotency table.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
