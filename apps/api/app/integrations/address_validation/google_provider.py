"""Google Address Validation API (v1) provider -- the default/primary
provider whenever `GOOGLE_ADDRESS_VALIDATION_API_KEY` is set (see
`config.py`). Real, documented external API:
https://developers.google.com/maps/documentation/address-validation

One address per request -- this API has no bulk/batch endpoint, so
`validate_batch` fans out one `validateAddress` call per address
(bounded concurrency) rather than a single round trip. That's still
safe for this feature's call sites: validation only ever runs at
write-time for a single order, or for the explicit one-order
"Validate Address" action -- never for a whole page of orders at once.

The API key is sent via the `X-Goog-Api-Key` header (Google's documented
alternative to the `?key=` query parameter) specifically so it can never
end up in a URL that gets logged -- by httpx, a proxy, or an exception
message that includes the request URL.

Google's response has no single 0-100 "score" field; `_parse_result`
derives one from real, documented response fields (`verdict.
addressComplete`, `hasUnconfirmedComponents`/`hasReplacedComponents`,
each `addressComponents[].confirmationLevel`, `missingComponentTypes`,
`unresolvedTokens`) -- never a random or placeholder number. Every
failure mode (non-2xx, timeout, malformed JSON, unexpected shape)
degrades to `UNKNOWN`, never `VALID`, and is logged without the request
body/headers so the API key is never written to logs.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.address_validation.config import GoogleAddressValidationConfig
from app.integrations.address_validation.provider import (
    AddressValidationProvider,
    AddressValidationResult,
)
from app.models.enums import AddressValidationStatus

logger = get_logger(__name__)

_TIMEOUT_SECONDS = 15.0
_MAX_CONCURRENCY = 5

_CONFIRMED = "CONFIRMED"
_SUSPICIOUS = "UNCONFIRMED_AND_SUSPICIOUS"

# This OMS ships domestically (India) today -- unrecognized/blank
# country values default to "IN" rather than being sent to Google
# un-set, since `regionCode` materially improves match accuracy. A
# genuinely international order would need this table extended.
_REGION_CODE_ALIASES = {
    "india": "IN",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "united kingdom": "GB",
    "uk": "GB",
}


def _region_code(country: str | None) -> str:
    if not country:
        return "IN"
    trimmed = country.strip()
    if len(trimmed) == 2:
        return trimmed.upper()
    return _REGION_CODE_ALIASES.get(trimmed.lower(), "IN")


def _build_request_payload(address: dict) -> dict[str, Any]:
    address_lines = [line for line in (address.get("line1"), address.get("line2")) if line]
    payload_address: dict[str, Any] = {"regionCode": _region_code(address.get("country"))}
    if address_lines:
        payload_address["addressLines"] = address_lines
    if address.get("city"):
        payload_address["locality"] = address["city"]
    if address.get("state"):
        payload_address["administrativeArea"] = address["state"]
    if address.get("pin_code"):
        payload_address["postalCode"] = address["pin_code"]
    return {"address": payload_address}


def _parse_result(payload: dict) -> AddressValidationResult:
    result = payload.get("result")
    if not isinstance(result, dict):
        return AddressValidationResult(
            status=AddressValidationStatus.UNKNOWN,
            score=0,
            reason="Google Address Validation API returned an unexpected response shape.",
        )

    verdict = result.get("verdict") or {}
    address_result = result.get("address") or {}
    components = address_result.get("addressComponents") or []
    missing = address_result.get("missingComponentTypes") or []
    unresolved = address_result.get("unresolvedTokens") or []

    total = len(components)
    confirmed = sum(1 for c in components if c.get("confirmationLevel") == _CONFIRMED)
    suspicious = sum(1 for c in components if c.get("confirmationLevel") == _SUSPICIOUS)

    address_complete = bool(verdict.get("addressComplete"))
    has_unconfirmed = bool(verdict.get("hasUnconfirmedComponents"))
    has_replaced = bool(verdict.get("hasReplacedComponents"))

    # The closest real, derived analogue to a 0-100 confidence Google's
    # API offers -- it has no such field itself. Based purely on the
    # fraction of address components Google could actually confirm.
    score = round(100 * confirmed / total) if total else (100 if address_complete else 0)
    if missing or unresolved:
        score = min(score, 60)
    if suspicious:
        score = min(score, 25)
    score = max(0, min(100, score))

    fully_confirmed = (
        address_complete
        and not has_unconfirmed
        and not has_replaced
        and not missing
        and not unresolved
    )
    if suspicious:
        status = AddressValidationStatus.JUNK
    elif fully_confirmed or score >= 80:
        status = AddressValidationStatus.VALID
    elif score >= 40:
        status = AddressValidationStatus.AMBIGUOUS
    else:
        status = AddressValidationStatus.JUNK

    reason_parts: list[str] = []
    if missing:
        reason_parts.append(f"Missing: {', '.join(missing)}")
    if unresolved:
        reason_parts.append(f"Unrecognized text: {', '.join(unresolved)}")
    if suspicious:
        reason_parts.append(f"{suspicious} address component(s) flagged as suspicious by Google")
    if has_replaced:
        reason_parts.append("Some components were corrected by Google")
    if not reason_parts:
        reason_parts.append("Address confirmed by Google Address Validation API.")

    provider_ref = payload.get("responseId")
    return AddressValidationResult(
        status=status,
        score=score,
        reason="; ".join(reason_parts),
        provider_ref=str(provider_ref) if provider_ref else None,
    )


class GoogleAddressValidationProvider(AddressValidationProvider):
    """`client` is injectable (a `httpx.AsyncClient` backed by
    `httpx.MockTransport` in tests, matching this repo's existing
    Cashfree/Shiprocket client-testing convention) -- a fresh client is
    opened per call otherwise.
    """

    def __init__(
        self, config: GoogleAddressValidationConfig, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._config = config
        self._client = client

    async def validate_batch(self, addresses: list[dict]) -> list[AddressValidationResult]:
        if not addresses:
            return []
        semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def _bounded(address: dict) -> AddressValidationResult:
            async with semaphore:
                return await self._validate_one(address)

        return list(await asyncio.gather(*(_bounded(a) for a in addresses)))

    async def _validate_one(self, address: dict) -> AddressValidationResult:
        headers = {"X-Goog-Api-Key": self._config.api_key, "Content-Type": "application/json"}
        payload = _build_request_payload(address)
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._config.api_url, json=payload, headers=headers
                )
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                    response = await client.post(
                        self._config.api_url, json=payload, headers=headers
                    )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "google_address_validation_http_error", status_code=exc.response.status_code
            )
            return AddressValidationResult(
                status=AddressValidationStatus.UNKNOWN,
                score=0,
                reason="Google Address Validation API request failed.",
            )
        except httpx.HTTPError:
            logger.warning("google_address_validation_request_failed")
            return AddressValidationResult(
                status=AddressValidationStatus.UNKNOWN,
                score=0,
                reason="Google Address Validation API request failed.",
            )
        except ValueError:
            logger.warning("google_address_validation_invalid_json")
            return AddressValidationResult(
                status=AddressValidationStatus.UNKNOWN,
                score=0,
                reason="Google Address Validation API returned an unreadable response.",
            )

        if not isinstance(body, dict):
            return AddressValidationResult(
                status=AddressValidationStatus.UNKNOWN,
                score=0,
                reason="Google Address Validation API returned an unexpected response shape.",
            )
        return _parse_result(body)
