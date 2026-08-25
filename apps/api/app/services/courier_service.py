from __future__ import annotations

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.courier import Courier
from app.repositories.courier import CourierRepository
from app.schemas.common import PageParams, SortParams


class CourierService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.couriers = CourierRepository(session)

    async def list_couriers(
        self, *, page_params: PageParams, sort_params: SortParams
    ) -> tuple[list[Courier], int]:
        items, total = await self.couriers.list(page_params=page_params, sort_params=sort_params)
        return list(items), total

    async def get_courier(self, courier_id: uuid.UUID) -> Courier:
        courier = await self.couriers.get_by_id(courier_id)
        if courier is None:
            raise NotFoundError("Courier not found.")
        return courier

    async def create_courier(self, **fields) -> Courier:  # noqa: ANN003
        if await self.couriers.get_by_code(fields["code"]) is not None:
            raise ConflictError(f"Courier code '{fields['code']}' already exists.")
        courier = await self.couriers.create(**fields)
        await self.session.commit()
        return courier

    async def update_courier(self, courier_id: uuid.UUID, **fields) -> Courier:  # noqa: ANN003
        courier = await self.get_courier(courier_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        if clean:
            await self.couriers.update(courier, **clean)
        await self.session.commit()
        return courier

    async def upsert_synced_courier(
        self,
        *,
        source_system: str,
        external_id: str,
        name: str,
        courier_metadata: dict | None = None,
    ) -> tuple[Courier, bool]:
        """Idempotent create-or-update keyed by `(source_system, external_id)`
        — e.g. Shiprocket's numeric `courier_company_id`. `code` (required,
        unique) has no equivalent in Shiprocket's courier data, so a new
        courier gets a name-derived slug, de-duplicated against existing
        codes rather than assumed unique.
        """
        existing = await self.couriers.get_by_source_external_id(
            source_system=source_system, external_id=external_id
        )
        if existing is not None:
            await self.couriers.update(existing, name=name, courier_metadata=courier_metadata)
            await self.session.commit()
            return existing, False

        code = await self._unique_code_for(name, external_id)
        courier, created = await self.couriers.upsert_by_external_id(
            source_system=source_system,
            external_id=external_id,
            name=name,
            code=code,
            courier_metadata=courier_metadata,
        )
        await self.session.commit()
        return courier, created

    async def _unique_code_for(self, name: str, external_id: str) -> str:
        base = (
            re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or f"courier-{external_id}"
        )
        candidate = base
        suffix = 0
        while await self.couriers.get_by_code(candidate) is not None:
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate
