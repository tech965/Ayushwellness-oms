"""Generic, configurable HTTP address-validation provider -- used only
when `ADDRESS_VALIDATION_API_URL` is set (see `config.py`); the default
`HeuristicAddressValidationProvider` runs otherwise.

IMPORTANT / HONESTY NOTE: no specific vendor is hardcoded here, and this
class has NOT been exercised against any real, live address-validation
API -- there is no such account/credentials available in this
engagement to test against. It implements a plain, generic contract
(POST a batch of addresses, expect a JSON array of `{status, score,
reason, provider_ref}` results back) that a real provider would need to
be adapted to match (or fronted by a small translation service) before
this class can be trusted in production. Every failure mode --
non-2xx, timeout, malformed JSON, a response of the wrong shape --
degrades to `UNKNOWN` for the affected address(es), never `VALID`, and
is logged so a misconfigured/broken provider is visible in production
logs rather than silently producing wrong results.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.address_validation.config import AddressValidationConfig
from app.integrations.address_validation.provider import (
    AddressValidationProvider,
    AddressValidationResult,
)
from app.models.enums import AddressValidationStatus

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 15.0

_STATUS_MAP = {
    "valid": AddressValidationStatus.VALID,
    "ambiguous": AddressValidationStatus.AMBIGUOUS,
    "junk": AddressValidationStatus.JUNK,
    "unknown": AddressValidationStatus.UNKNOWN,
}


def _unknown_results(count: int, reason: str) -> list[AddressValidationResult]:
    return [
        AddressValidationResult(status=AddressValidationStatus.UNKNOWN, score=0, reason=reason)
        for _ in range(count)
    ]


class HttpAddressValidationProvider(AddressValidationProvider):
    def __init__(self, config: AddressValidationConfig) -> None:
        self._config = config

    async def validate_batch(self, addresses: list[dict]) -> list[AddressValidationResult]:
        if not addresses:
            return []
        headers = {}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    self._config.api_url, json={"addresses": addresses}, headers=headers
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "address_validation_http_provider_request_failed",
                error=str(exc),
                address_count=len(addresses),
            )
            return _unknown_results(len(addresses), "Address validation provider request failed.")
        except ValueError:
            # response.json() raised -- not valid JSON.
            logger.warning(
                "address_validation_http_provider_invalid_json", address_count=len(addresses)
            )
            return _unknown_results(
                len(addresses), "Address validation provider returned an unreadable response."
            )

        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list) or len(raw_results) != len(addresses):
            logger.warning(
                "address_validation_http_provider_unexpected_shape",
                address_count=len(addresses),
                got_count=len(raw_results) if isinstance(raw_results, list) else None,
            )
            return _unknown_results(
                len(addresses), "Address validation provider returned an unexpected response shape."
            )

        results: list[AddressValidationResult] = []
        for raw in raw_results:
            results.append(self._parse_one(raw))
        return results

    def _parse_one(self, raw: Any) -> AddressValidationResult:
        if not isinstance(raw, dict):
            return AddressValidationResult(
                status=AddressValidationStatus.UNKNOWN,
                score=0,
                reason="Address validation provider returned an unexpected item shape.",
            )
        status = _STATUS_MAP.get(str(raw.get("status", "")).strip().lower())
        if status is None:
            return AddressValidationResult(
                status=AddressValidationStatus.UNKNOWN,
                score=0,
                reason=f"Address validation provider returned an unrecognized status "
                f"('{raw.get('status')}').",
            )
        raw_score = raw.get("score")
        score = int(raw_score) if isinstance(raw_score, (int, float)) else 0
        score = max(0, min(100, score))
        return AddressValidationResult(
            status=status,
            score=score,
            reason=raw.get("reason"),
            provider_ref=(str(raw["provider_ref"]) if raw.get("provider_ref") else None),
        )
