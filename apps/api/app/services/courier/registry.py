"""Resolves a `source_system` code to its `CourierProvider` implementation.

Deliberately NOT wired into any existing fulfillment call site yet
(Fulfillment Queue, order detail page, NDR flow all still call
`ShiprocketOperationsService` directly) — this exists so that future
call-site migration has one resolver to depend on, not scattered
if/elif provider checks. An unknown or unimplemented provider code
raises rather than silently falling back to Shiprocket — a caller must
never have an explicit NimbusPost request quietly become a Shiprocket
shipment.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.mixins import SourceSystem
from app.services.courier.base import CourierProvider
from app.services.courier.nimbuspost_provider import NimbusPostProvider
from app.services.courier.shiprocket_provider import ShiprocketProvider

_PROVIDERS: dict[str, type[CourierProvider]] = {
    SourceSystem.SHIPROCKET: ShiprocketProvider,
    SourceSystem.NIMBUSPOST: NimbusPostProvider,
}


def get_courier_provider(source_system: str, session: AsyncSession) -> CourierProvider:
    """`session` is only used by providers that need it (`ShiprocketProvider`
    wraps a session-bound `ShiprocketOperationsService`); `NimbusPostProvider`
    currently ignores it since every operation raises before needing one --
    every provider still takes the same constructor argument so this
    resolver never needs a per-class special case.
    """
    provider_cls = _PROVIDERS.get(source_system)
    if provider_cls is None:
        raise ValidationError(
            f"No courier provider registered for '{source_system}'.",
            details={"error_type": "unknown_provider", "source_system": source_system},
        )
    return provider_cls(session)
