"""Provider interface for address validation.

Batch-first by design (`validate_batch`) so a real provider that
supports bulk requests can honor it in one round trip --
`AddressValidationService` never calls a provider once per order in a
loop when a batch call is available; `validate` (one address) is a
convenience every concrete provider gets for free.

`AddressValidationResult.status` is always one of the normalized
`AddressValidationStatus` values (`app.models.enums`), independent of
whatever vocabulary a specific provider uses internally -- a provider
implementation is responsible for mapping its own response into this
shape, never the caller.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.enums import AddressValidationStatus


@dataclass(frozen=True)
class AddressValidationResult:
    status: AddressValidationStatus
    # 0-100 -- always paired with `status`, never shown alone (matches
    # the Shiprocket-style "82% -- Valid Address" display this feature
    # is modeled on).
    score: int
    reason: str | None = None
    # Whatever reference/request id the provider itself returned, if
    # any -- `None` for the built-in heuristic provider, which has no
    # such concept. Support/debugging only, never read by business logic.
    provider_ref: str | None = None


class AddressValidationProvider(ABC):
    @abstractmethod
    async def validate_batch(self, addresses: list[dict]) -> list[AddressValidationResult]:
        """One result per input address, same order, same length --
        never fewer, never reordered, so the caller can zip them back
        onto the orders it came from. Must never raise for a single bad
        address; a provider-side failure for one item is reported as
        that item's own `AddressValidationResult` (`UNKNOWN`, see this
        module's docstring), not an exception that would lose every
        other address in the batch.
        """
        raise NotImplementedError

    async def validate(self, address: dict) -> AddressValidationResult:
        results = await self.validate_batch([address])
        return results[0]
