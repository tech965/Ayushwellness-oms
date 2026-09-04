"""AppSettings repository — single-row singleton (see `app.models.settings`
for why this is one JSON blob rather than a normal repository's
list/filter surface)."""

from __future__ import annotations

from sqlalchemy import select

from app.models.settings import AppSettings
from app.repositories.base import BaseRepository


class SettingsRepository(BaseRepository[AppSettings]):
    model = AppSettings

    async def get_or_create(self) -> AppSettings:
        existing = (await self.session.execute(select(AppSettings).limit(1))).scalar_one_or_none()
        if existing is not None:
            return existing
        return await self.create(values={})
