from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    phone: str | None = None
    password: str = Field(min_length=8, max_length=128)
    role_ids: list[uuid.UUID] = []


class UserUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = None
    is_active: bool | None = None
    role_ids: list[uuid.UUID] | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    phone: str | None = None
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime
    roles: list[str] = []
