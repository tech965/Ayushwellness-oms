from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str
    previous_value: dict | None
    new_value: dict | None
    ip_address: str | None
    user_agent: str | None
    audit_metadata: dict | None
    created_at: datetime
