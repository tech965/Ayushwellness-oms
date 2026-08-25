from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    module: str
    action: str
    description: str | None


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    description: str | None = None
    permission_ids: list[uuid.UUID] = []


class RoleUpdateRequest(BaseModel):
    description: str | None = None
    permission_ids: list[uuid.UUID] | None = None


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    permissions: list[str] = []
