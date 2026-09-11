"""GoogleAddressValidationProvider: request mapping (address ->
Google's `address` request shape, API key via header never URL/query),
response parsing (Google's real `verdict`/`addressComponents` fields ->
our VALID/AMBIGUOUS/JUNK/UNKNOWN + 0-100 score), and that every failure
mode degrades to UNKNOWN, never VALID. Uses `httpx.MockTransport` -- no
real Google account, matching this repo's Cashfree/Shiprocket client
test convention.

Also covers `get_address_validation_provider()`'s priority order: Google
> generic HTTP > heuristic.
"""

from __future__ import annotations

import httpx
import pytest
from app.integrations.address_validation.config import (
    AddressValidationConfig,
    GoogleAddressValidationConfig,
    get_address_validation_provider,
)
from app.integrations.address_validation.google_provider import (
    GoogleAddressValidationProvider,
    _build_request_payload,
    _region_code,
)
from app.integrations.address_validation.heuristic_provider import (
    HeuristicAddressValidationProvider,
)
from app.integrations.address_validation.http_provider import HttpAddressValidationProvider
from app.models.enums import AddressValidationStatus

pytestmark = pytest.mark.asyncio

_CONFIG = GoogleAddressValidationConfig(
    api_key="test-google-key",
    api_url="https://addressvalidation.googleapis.com/v1:validateAddress",
)

_ADDRESS = {
    "line1": "273 House No Ganpati Chowk",
    "line2": "Patidar Dharmsala",
    "city": "Mandsaur",
    "state": "Madhya Pradesh",
    "country": "India",
    "pin_code": "458556",
    "contact_name": "Ravi Kumar",
    "contact_phone": "9990000001",
    "is_default": False,
}


def _provider_with(handler) -> GoogleAddressValidationProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return GoogleAddressValidationProvider(_CONFIG, client=client)


def _confirmed_component(component_type: str) -> dict:
    return {"componentType": component_type, "confirmationLevel": "CONFIRMED"}


# --- request mapping ---


def test_region_code_maps_india_and_passes_through_iso_codes() -> None:
    assert _region_code("India") == "IN"
    assert _region_code("india") == "IN"
    assert _region_code("us") == "US"
    assert _region_code(None) == "IN"
    assert _region_code("Some Unrecognized Country") == "IN"


def test_request_payload_maps_our_address_fields_to_googles_shape() -> None:
    payload = _build_request_payload(_ADDRESS)
    assert payload == {
        "address": {
            "regionCode": "IN",
            "addressLines": ["273 House No Ganpati Chowk", "Patidar Dharmsala"],
            "locality": "Mandsaur",
            "administrativeArea": "Madhya Pradesh",
            "postalCode": "458556",
        }
    }


async def test_api_key_is_sent_via_header_never_in_the_url_or_query_string() -> None:
    seen = {"url": None, "headers": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(
            200,
            json={
                "result": {
                    "verdict": {"addressComplete": True},
                    "address": {"addressComponents": [_confirmed_component("locality")]},
                },
                "responseId": "resp-1",
            },
        )

    provider = _provider_with(handler)
    await provider.validate(_ADDRESS)

    assert "key=" not in seen["url"]
    assert "test-google-key" not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == "test-google-key"


# --- response parsing ---


async def test_complete_confirmed_address_is_valid_with_a_high_score() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "verdict": {
                        "addressComplete": True,
                        "hasUnconfirmedComponents": False,
                        "hasReplacedComponents": False,
                    },
                    "address": {
                        "addressComponents": [
                            _confirmed_component("street_number"),
                            _confirmed_component("locality"),
                            _confirmed_component("postal_code"),
                        ],
                        "missingComponentTypes": [],
                        "unresolvedTokens": [],
                    },
                },
                "responseId": "resp-valid-1",
            },
        )

    provider = _provider_with(handler)
    result = await provider.validate(_ADDRESS)

    assert result.status == AddressValidationStatus.VALID
    assert result.score == 100
    assert result.provider_ref == "resp-valid-1"
    assert "confirmed" in result.reason.lower()


async def test_missing_components_make_the_address_ambiguous_not_valid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "verdict": {
                        "addressComplete": False,
                        "hasUnconfirmedComponents": True,
                        "hasReplacedComponents": False,
                    },
                    "address": {
                        "addressComponents": [
                            _confirmed_component("street_number"),
                            {
                                "componentType": "locality",
                                "confirmationLevel": "UNCONFIRMED_BUT_PLAUSIBLE",
                            },
                        ],
                        "missingComponentTypes": ["administrative_area"],
                        "unresolvedTokens": [],
                    },
                },
                "responseId": "resp-ambiguous-1",
            },
        )

    provider = _provider_with(handler)
    result = await provider.validate(_ADDRESS)

    assert result.status == AddressValidationStatus.AMBIGUOUS
    assert result.score < 80
    assert "administrative_area" in result.reason


async def test_suspicious_component_makes_the_address_junk_regardless_of_other_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "verdict": {"addressComplete": True},
                    "address": {
                        "addressComponents": [
                            _confirmed_component("street_number"),
                            {
                                "componentType": "locality",
                                "confirmationLevel": "UNCONFIRMED_AND_SUSPICIOUS",
                            },
                        ],
                        "missingComponentTypes": [],
                        "unresolvedTokens": [],
                    },
                },
                "responseId": "resp-junk-1",
            },
        )

    provider = _provider_with(handler)
    result = await provider.validate(_ADDRESS)

    assert result.status == AddressValidationStatus.JUNK
    assert result.score <= 25
    assert "suspicious" in result.reason.lower()


# --- failure modes: every one must degrade to UNKNOWN, never VALID ---


async def test_non_2xx_response_degrades_to_unknown_never_valid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "API key invalid"}})

    provider = _provider_with(handler)
    result = await provider.validate(_ADDRESS)

    assert result.status == AddressValidationStatus.UNKNOWN
    assert result.score == 0


async def test_malformed_json_degrades_to_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    provider = _provider_with(handler)
    result = await provider.validate(_ADDRESS)

    assert result.status == AddressValidationStatus.UNKNOWN


async def test_unexpected_response_shape_degrades_to_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"nothing": "useful"})

    provider = _provider_with(handler)
    result = await provider.validate(_ADDRESS)

    assert result.status == AddressValidationStatus.UNKNOWN


async def test_connection_failure_degrades_to_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider_with(handler)
    result = await provider.validate(_ADDRESS)

    assert result.status == AddressValidationStatus.UNKNOWN


async def test_validate_batch_preserves_order_and_length_across_multiple_addresses() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        idx = len(calls)
        return httpx.Response(
            200,
            json={
                "result": {
                    "verdict": {"addressComplete": True},
                    "address": {"addressComponents": [_confirmed_component("locality")]},
                },
                "responseId": f"resp-{idx}",
            },
        )

    provider = _provider_with(handler)
    results = await provider.validate_batch([_ADDRESS, {**_ADDRESS, "city": "Indore"}])

    assert len(results) == 2
    assert {r.provider_ref for r in results} == {"resp-1", "resp-2"}


# --- provider selection precedence ---


async def test_google_provider_is_selected_when_its_key_is_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        GoogleAddressValidationConfig,
        "from_settings",
        classmethod(lambda cls: _CONFIG),
    )
    provider = get_address_validation_provider()
    assert isinstance(provider, GoogleAddressValidationProvider)


async def test_generic_http_provider_used_only_when_google_is_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        GoogleAddressValidationConfig, "from_settings", classmethod(lambda cls: None)
    )
    monkeypatch.setattr(
        AddressValidationConfig,
        "from_settings",
        classmethod(
            lambda cls: AddressValidationConfig(api_url="https://example.test", api_key=None)
        ),
    )
    provider = get_address_validation_provider()
    assert isinstance(provider, HttpAddressValidationProvider)


async def test_heuristic_provider_used_only_when_no_real_provider_is_configured(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        GoogleAddressValidationConfig, "from_settings", classmethod(lambda cls: None)
    )
    monkeypatch.setattr(AddressValidationConfig, "from_settings", classmethod(lambda cls: None))
    provider = get_address_validation_provider()
    assert isinstance(provider, HeuristicAddressValidationProvider)
