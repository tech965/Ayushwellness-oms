from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class IntegrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    type: str
    status: str
    enabled: bool
    configuration: dict[str, Any] | None
    last_sync_at: datetime | None
    last_successful_sync_at: datetime | None
    last_failure_at: datetime | None
    last_failure_message: str | None
    created_at: datetime
    updated_at: datetime


class IntegrationHealthResponse(BaseModel):
    connected: bool
    status: str
    response_time_ms: float | None = None
    last_successful_sync_at: datetime | None = None
    last_failure_at: datetime | None = None
    error_message: str | None = None


class SyncJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    integration_id: uuid.UUID
    sync_type: str
    entity_type: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    records_received: int
    records_created: int
    records_updated: int
    records_skipped: int
    records_failed: int
    error_count: int
    job_metadata: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class SyncErrorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sync_job_id: uuid.UUID
    integration_id: uuid.UUID
    entity_type: str
    external_id: str | None
    error_type: str
    error_message: str
    payload_reference: str | None
    retry_count: int
    resolved: bool
    resolved_at: datetime | None
    created_at: datetime


class WebhookEventResponse(BaseModel):
    """Deliberately excludes `payload` — spec §18: don't expose raw
    webhook payload data by default on a monitoring endpoint.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    integration_id: uuid.UUID
    event_type: str
    external_event_id: str
    external_resource_id: str | None
    received_at: datetime
    processed_at: datetime | None
    status: str
    retry_count: int
    error_message: str | None
    created_at: datetime


class TriggerSyncRequest(BaseModel):
    sync_type: str = "incremental"
    entity_type: str
