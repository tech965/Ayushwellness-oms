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
    # Set only for Telecaller accounts — which Team Leader they report to.
    # Reuses the existing Users admin CRUD rather than a parallel "team
    # management" endpoint (a Telecaller belongs to exactly one team
    # leader; a "team" is just "the users with this team_leader_id").
    team_leader_id: uuid.UUID | None = None


class UserUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = None
    is_active: bool | None = None
    role_ids: list[uuid.UUID] | None = None
    team_leader_id: uuid.UUID | None = None
    # Distinguishes "field omitted, leave unchanged" from "explicitly
    # clear the team leader" — `team_leader_id: None` alone is ambiguous
    # between those two under a partial-update PATCH.
    clear_team_leader: bool = False


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    phone: str | None = None
    is_active: bool
    is_superuser: bool
    team_leader_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    roles: list[str] = []
