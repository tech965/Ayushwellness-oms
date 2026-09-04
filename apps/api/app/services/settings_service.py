"""OMS Settings — get/update the single `AppSettings` row.

`values` (a JSON blob) is parsed against `AppSettingsData` on every read,
so any field added to the schema later shows up with its default the
moment an old stored blob is read back, with no migration/backfill step
needed. `update_settings` merges section-by-section (only the sections
present in the request are replaced) so saving one card (e.g. "Dashboard")
never clobbers sections the user hasn't touched (e.g. "Security").
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import AppSettings
from app.repositories.settings import SettingsRepository
from app.schemas.settings import AppSettingsData, AppSettingsResponse, AppSettingsUpdateRequest


class SettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = SettingsRepository(session)

    async def get_settings(self) -> AppSettingsResponse:
        row = await self.repository.get_or_create()
        # `get_or_create` may have just inserted the singleton row -- the
        # request-scoped session (`app.db.session.get_db`) never commits
        # on its own, so a bare read on a fresh database would otherwise
        # silently roll that insert back at the end of the request.
        await self.session.commit()
        return await self._to_response(row)

    async def update_settings(
        self, payload: AppSettingsUpdateRequest, actor_id: uuid.UUID | None
    ) -> AppSettingsResponse:
        row = await self.repository.get_or_create()
        merged_values = AppSettingsData.model_validate(row.values or {}).model_dump(mode="json")
        for section, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
            merged_values[section] = value
        # Round-trips through the schema once more so a partially-invalid
        # stored blob from an older schema version can't silently survive
        # a merge -- every section in the persisted JSON is always a
        # fully valid `AppSettingsData`.
        merged = AppSettingsData.model_validate(merged_values)
        await self.repository.update(
            row, values=merged.model_dump(mode="json"), updated_by_user_id=actor_id
        )
        await self.session.commit()
        return await self._to_response(row)

    async def _to_response(self, row: AppSettings) -> AppSettingsResponse:
        data = AppSettingsData.model_validate(row.values or {})
        updated_by_email = None
        if row.updated_by_user_id is not None:
            await self.session.refresh(row, attribute_names=["updated_by"])
            updated_by_email = row.updated_by.email if row.updated_by else None
        return AppSettingsResponse(
            settings=data, updated_at=row.updated_at, updated_by_email=updated_by_email
        )
