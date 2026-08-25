"""Generic repository base class.

Routes never touch SQLAlchemy directly — every query goes through a
repository, and every repository that wraps a historical/append-only
model (`OrderEventRepository`, `ShipmentEventRepository`,
`AuditLogRepository`) intentionally exposes only `create`/`list`, not
`update`/`delete`, so the "never mutate history" rule is enforced by the
type, not just by convention.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.schemas.common import PageParams, SortParams


class BaseRepository[ModelT: Base]:
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, id_: uuid.UUID) -> ModelT | None:
        return await self.session.get(self.model, id_)

    async def create(self, **fields: Any) -> ModelT:
        instance = self.model(**fields)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def update(self, instance: ModelT, **fields: Any) -> ModelT:
        for key, value in fields.items():
            setattr(instance, key, value)
        await self.session.flush()
        return instance

    def _base_query(self) -> Select:
        return select(self.model)

    async def list(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams | None = None,
        query: Select | None = None,
        default_sort_column: str = "created_at",
    ) -> tuple[Sequence[ModelT], int]:
        stmt = query if query is not None else self._base_query()

        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))

        sort_column_name = (sort_params.sort_by if sort_params else None) or default_sort_column
        sort_column = getattr(self.model, sort_column_name, None)
        if sort_column is not None:
            order = (
                sort_column.desc()
                if (not sort_params or sort_params.sort_order == "desc")
                else sort_column.asc()
            )
            stmt = stmt.order_by(order)

        stmt = stmt.offset(page_params.offset).limit(page_params.page_size)
        result = await self.session.execute(stmt)
        return result.scalars().all(), total or 0

    async def get_by_source_external_id(
        self, *, source_system: str, external_id: str
    ) -> ModelT | None:
        """Only meaningful for models with `SyncMetadataMixin`. `ModelT` is
        bound to `Base` (not the mixin) so every repository shares one
        `BaseRepository`; the attribute access below is valid for every
        repository that actually calls this method.
        """
        stmt = select(self.model).where(
            self.model.source_system == source_system,  # type: ignore[attr-defined]
            self.model.external_id == external_id,  # type: ignore[attr-defined]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_by_external_id(
        self, *, source_system: str, external_id: str, **fields: Any
    ) -> tuple[ModelT, bool]:
        """Idempotent create-or-update keyed by (source_system, external_id).

        Dialect-portable by design (no `ON CONFLICT`), so the exact same
        path runs in production Postgres and the SQLite test suite. The
        unique constraint on (source_system, external_id) is the race
        backstop: a concurrent insert that slips past the initial SELECT
        raises IntegrityError, which is caught and retried once as an
        update. Returns (instance, created).
        """
        existing = await self.get_by_source_external_id(
            source_system=source_system, external_id=external_id
        )
        if existing is not None:
            return await self.update(existing, **fields), False

        try:
            instance = await self.create(
                source_system=source_system, external_id=external_id, **fields
            )
            return instance, True
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_source_external_id(
                source_system=source_system, external_id=external_id
            )
            if existing is None:
                raise
            return await self.update(existing, **fields), False


class AppendOnlyRepository[ModelT: Base](BaseRepository[ModelT]):
    """Base for history/audit models: create + list only."""
