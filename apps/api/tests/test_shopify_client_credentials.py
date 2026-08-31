"""Shopify Client Credentials Grant — token acquisition, in-memory
caching, refresh-before-expiry, concurrency-safe single-flight refresh,
and the GraphQL 401-reauth-retry-once path in `ShopifyClient.execute()`.

No real Shopify account — `_StubHttpxClient` stands in for
`httpx.AsyncClient`, routing by URL substring so a single test can stub
both the token endpoint and the GraphQL endpoint independently.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from app.integrations.shopify.auth import ShopifyTokenManager
from app.integrations.shopify.client import ShopifyClient
from app.integrations.shopify.config import ShopifyConfig
from app.integrations.shopify.errors import ShopifyApiError

TOKEN_URL_MARKER = "/admin/oauth/access_token"
GRAPHQL_URL_MARKER = "/graphql.json"


def _http_response(status_code: int, json_body: dict) -> httpx.Response:
    request = httpx.Request("POST", "https://aayushveda.myshopify.com/x")
    return httpx.Response(status_code=status_code, json=json_body, request=request)


def _token_response(
    *,
    token: str = "shpat_fake_token",
    expires_in: int = 86399,
    scope: str = "read_customers,read_orders,read_products",
) -> httpx.Response:
    return _http_response(200, {"access_token": token, "scope": scope, "expires_in": expires_in})


class _StubHttpxClient:
    """Routes `.post(url, ...)` by URL substring to an independent queue
    of canned `httpx.Response`s (or exceptions to raise) per endpoint.
    """

    def __init__(self, **queues: list) -> None:
        self._queues = {marker: list(items) for marker, items in queues.items()}
        self.calls: list[tuple[str, dict | None, dict | None, dict | None]] = []

    async def post(
        self,
        url: str,
        *,
        json: dict | None = None,
        data: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        self.calls.append((url, json, data, headers))
        for marker, queue in self._queues.items():
            if marker in url:
                if not queue:
                    raise AssertionError(f"No stubbed response left for {marker}")
                item = queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
        raise AssertionError(f"Unexpected URL posted to: {url}")

    @property
    def token_calls(self) -> list:
        return [c for c in self.calls if TOKEN_URL_MARKER in c[0]]

    @property
    def graphql_calls(self) -> list:
        return [c for c in self.calls if GRAPHQL_URL_MARKER in c[0]]


def _make_token_manager(http_client: _StubHttpxClient, **overrides: object) -> ShopifyTokenManager:
    return ShopifyTokenManager(
        shop_domain="aayushveda.myshopify.com",
        client_id="test-client-id",
        client_secret="test-client-secret",
        http_client=http_client,  # type: ignore[arg-type]
        **overrides,  # type: ignore[arg-type]
    )


# --- ShopifyConfig: which auth mode wins ------------------------------


def test_config_prefers_client_credentials_when_both_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "SHOPIFY_STORE_DOMAIN", "aayushveda.myshopify.com")
    monkeypatch.setattr(settings, "SHOPIFY_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "SHOPIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(settings, "SHOPIFY_ACCESS_TOKEN", "shpat_stale_permanent_token")

    config = ShopifyConfig.from_settings()

    assert config is not None
    assert config.uses_client_credentials is True
    assert config.client_id == "cid"
    # Never silently keeps using the stale permanent token once client
    # credentials are configured.
    assert config.access_token is None


def test_config_falls_back_to_access_token_when_client_credentials_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "SHOPIFY_STORE_DOMAIN", "aayushveda.myshopify.com")
    monkeypatch.setattr(settings, "SHOPIFY_CLIENT_ID", None)
    monkeypatch.setattr(settings, "SHOPIFY_CLIENT_SECRET", None)
    monkeypatch.setattr(settings, "SHOPIFY_ACCESS_TOKEN", "shpat_static")

    config = ShopifyConfig.from_settings()

    assert config is not None
    assert config.uses_client_credentials is False
    assert config.access_token == "shpat_static"


def test_config_strips_incidental_whitespace_from_client_id_and_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real production bug this guards: a credential pasted into Render's
    dashboard can silently pick up a trailing newline/space -- invisible
    when eyeballing the value, but it makes the byte sequence sent to
    Shopify's token endpoint wrong, producing a 400 ("malformed request")
    that looks identical to a genuinely wrong client_id. The same class of
    bug was already fixed once for SHOPIFY_WEBHOOK_SECRET.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "SHOPIFY_STORE_DOMAIN", " aayushveda.myshopify.com\n")
    monkeypatch.setattr(settings, "SHOPIFY_CLIENT_ID", "cid\n")
    monkeypatch.setattr(settings, "SHOPIFY_CLIENT_SECRET", " csecret ")

    config = ShopifyConfig.from_settings()

    assert config is not None
    assert config.shop_domain == "aayushveda.myshopify.com"
    assert config.client_id == "cid"
    assert config.client_secret == "csecret"


def test_config_returns_none_when_nothing_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "SHOPIFY_STORE_DOMAIN", "aayushveda.myshopify.com")
    monkeypatch.setattr(settings, "SHOPIFY_CLIENT_ID", None)
    monkeypatch.setattr(settings, "SHOPIFY_CLIENT_SECRET", None)
    monkeypatch.setattr(settings, "SHOPIFY_ACCESS_TOKEN", None)

    assert ShopifyConfig.from_settings() is None


# --- Token acquisition -------------------------------------------------


@pytest.mark.asyncio
async def test_get_access_token_posts_the_correct_client_credentials_body() -> None:
    client = _StubHttpxClient(**{TOKEN_URL_MARKER: [_token_response(token="tok_abc")]})
    manager = _make_token_manager(client)

    token = await manager.get_access_token()

    assert token == "tok_abc"
    assert len(client.token_calls) == 1
    url, json_body, form_data, _headers = client.token_calls[0]
    assert url == "https://aayushveda.myshopify.com/admin/oauth/access_token"
    assert json_body is None
    assert form_data == {
        "grant_type": "client_credentials",
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
    }


@pytest.mark.asyncio
async def test_second_call_reuses_the_cached_token_without_a_second_request() -> None:
    client = _StubHttpxClient(**{TOKEN_URL_MARKER: [_token_response(token="tok_once")]})
    manager = _make_token_manager(client)

    first = await manager.get_access_token()
    second = await manager.get_access_token()

    assert first == second == "tok_once"
    assert len(client.token_calls) == 1


@pytest.mark.asyncio
async def test_token_is_refreshed_once_expired_and_expires_in_is_respected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _StubHttpxClient(
        **{
            TOKEN_URL_MARKER: [
                _token_response(token="tok_first", expires_in=600),
                _token_response(token="tok_second", expires_in=600),
            ]
        }
    )
    manager = _make_token_manager(client)

    fake_now = [1_000_000.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_now[0])

    first = await manager.get_access_token()
    assert first == "tok_first"

    # 100s later: well within expires_in=600 minus the 300s safety margin.
    fake_now[0] += 100
    still_cached = await manager.get_access_token()
    assert still_cached == "tok_first"
    assert len(client.token_calls) == 1

    # 500s further (600s total): past the safety-margin-adjusted expiry
    # (600 - 300 = 300s of real cache lifetime) -> must refresh.
    fake_now[0] += 500
    refreshed = await manager.get_access_token()
    assert refreshed == "tok_second"
    assert len(client.token_calls) == 2


@pytest.mark.asyncio
async def test_concurrent_callers_do_not_stampede_the_token_endpoint() -> None:
    """10 simultaneous callers with no cached token yet must collapse into
    exactly one real token request, not 10.
    """
    client = _StubHttpxClient(**{TOKEN_URL_MARKER: [_token_response(token="tok_shared")]})
    manager = _make_token_manager(client)

    results = await asyncio.gather(*(manager.get_access_token() for _ in range(10)))

    assert all(result == "tok_shared" for result in results)
    assert len(client.token_calls) == 1


@pytest.mark.asyncio
async def test_missing_access_token_in_response_raises_a_classified_error() -> None:
    client = _StubHttpxClient(**{TOKEN_URL_MARKER: [_http_response(200, {"expires_in": 86399})]})
    manager = _make_token_manager(client)

    with pytest.raises(ShopifyApiError) as exc_info:
        await manager.get_access_token()
    assert exc_info.value.error_type == "validation_error"


@pytest.mark.asyncio
async def test_invalid_client_credentials_raise_authentication_error() -> None:
    """The error must never leak the actual `client_secret` value."""
    client = _StubHttpxClient(
        **{TOKEN_URL_MARKER: [_http_response(401, {"errors": "invalid_client"})]}
    )
    manager = _make_token_manager(client)

    with pytest.raises(ShopifyApiError) as exc_info:
        await manager.get_access_token()

    assert exc_info.value.error_type == "authentication_error"
    assert "test-client-secret" not in exc_info.value.message


@pytest.mark.asyncio
async def test_token_endpoint_oauth_error_detail_is_surfaced_without_leaking_secrets() -> None:
    """Shopify's token endpoint returns a standard OAuth2 error body on
    failure (`{"error": ..., "error_description": ...}`) -- that text is
    Shopify's own stated reason, safe to surface, and turns a generic
    guess ("malformed request, or invalid SHOPIFY_CLIENT_ID format") into
    an actionable message for the next diagnosis.
    """
    client = _StubHttpxClient(
        **{
            TOKEN_URL_MARKER: [
                _http_response(
                    400,
                    {
                        "error": "invalid_request",
                        "error_description": "Unsupported grant_type parameter value",
                    },
                )
            ]
        }
    )
    manager = _make_token_manager(client)

    with pytest.raises(ShopifyApiError) as exc_info:
        await manager.get_access_token()

    assert exc_info.value.error_type == "validation_error"
    assert "invalid_request" in exc_info.value.message
    assert "Unsupported grant_type parameter value" in exc_info.value.message
    assert "test-client-secret" not in exc_info.value.message
    assert "test-client-id" not in exc_info.value.message


@pytest.mark.asyncio
async def test_invalidate_token_forces_a_fresh_fetch_on_the_next_call() -> None:
    client = _StubHttpxClient(
        **{
            TOKEN_URL_MARKER: [
                _token_response(token="tok_a", expires_in=86399),
                _token_response(token="tok_b", expires_in=86399),
            ]
        }
    )
    manager = _make_token_manager(client)

    first = await manager.get_access_token()
    assert first == "tok_a"

    manager.invalidate_token()

    second = await manager.get_access_token()
    assert second == "tok_b"
    assert len(client.token_calls) == 2


# --- ShopifyClient.execute(): GraphQL 401 -> invalidate + retry once ---


@pytest.mark.asyncio
async def test_graphql_401_triggers_one_reauth_retry_then_succeeds() -> None:
    client = _StubHttpxClient(
        **{
            TOKEN_URL_MARKER: [
                _token_response(token="tok_v1"),
                _token_response(token="tok_v2"),
            ],
            GRAPHQL_URL_MARKER: [
                _http_response(401, {"errors": [{"message": "Invalid API key or access token"}]}),
                _http_response(200, {"data": {"shop": {"name": "Aayush"}}}),
            ],
        }
    )
    config = ShopifyConfig(
        shop_domain="aayushveda.myshopify.com",
        api_version="2026-01",
        client_id="cid",
        client_secret="csecret",
    )
    shopify_client = ShopifyClient(config, http_client=client)  # type: ignore[arg-type]

    data = await shopify_client.execute("query { shop { name } }")

    assert data == {"shop": {"name": "Aayush"}}
    assert len(client.graphql_calls) == 2
    assert len(client.token_calls) == 2  # initial fetch + exactly one re-fetch

    # The retried GraphQL call used the freshly re-fetched token, not the
    # stale, just-rejected one.
    first_call_headers = client.graphql_calls[0][3]
    second_call_headers = client.graphql_calls[1][3]
    assert first_call_headers is not None
    assert first_call_headers["X-Shopify-Access-Token"] == "tok_v1"
    assert second_call_headers is not None
    assert second_call_headers["X-Shopify-Access-Token"] == "tok_v2"


@pytest.mark.asyncio
async def test_graphql_401_never_retries_more_than_once() -> None:
    client = _StubHttpxClient(
        **{
            TOKEN_URL_MARKER: [
                _token_response(token="tok_v1"),
                _token_response(token="tok_v2"),
            ],
            GRAPHQL_URL_MARKER: [
                _http_response(401, {"errors": [{"message": "Invalid API key or access token"}]}),
                _http_response(401, {"errors": [{"message": "Invalid API key or access token"}]}),
            ],
        }
    )
    config = ShopifyConfig(
        shop_domain="aayushveda.myshopify.com",
        api_version="2026-01",
        client_id="cid",
        client_secret="csecret",
    )
    shopify_client = ShopifyClient(config, http_client=client)  # type: ignore[arg-type]

    with pytest.raises(ShopifyApiError) as exc_info:
        await shopify_client.execute("query { shop { name } }")

    assert exc_info.value.error_type == "authentication_error"
    # Original attempt + exactly one reauth retry, never a third.
    assert len(client.graphql_calls) == 2


@pytest.mark.asyncio
async def test_static_access_token_mode_does_not_attempt_reauth_on_401() -> None:
    """A legacy SHOPIFY_ACCESS_TOKEN has nothing to refresh -- a 401 must
    behave exactly as it did before this migration (raise immediately,
    no token endpoint ever contacted).
    """
    client = _StubHttpxClient(
        **{
            GRAPHQL_URL_MARKER: [
                _http_response(401, {"errors": [{"message": "Invalid API key or access token"}]})
            ]
        }
    )
    config = ShopifyConfig(
        shop_domain="aayushveda.myshopify.com", api_version="2026-01", access_token="shpat_static"
    )
    shopify_client = ShopifyClient(config, http_client=client)  # type: ignore[arg-type]

    with pytest.raises(ShopifyApiError) as exc_info:
        await shopify_client.execute("query { shop { name } }")

    assert exc_info.value.error_type == "authentication_error"
    assert len(client.graphql_calls) == 1
    assert len(client.token_calls) == 0


@pytest.mark.asyncio
async def test_client_credentials_graphql_request_uses_a_valid_token_on_the_happy_path() -> None:
    client = _StubHttpxClient(
        **{
            TOKEN_URL_MARKER: [_token_response(token="tok_happy")],
            GRAPHQL_URL_MARKER: [_http_response(200, {"data": {"shop": {"name": "Aayush"}}})],
        }
    )
    config = ShopifyConfig(
        shop_domain="aayushveda.myshopify.com",
        api_version="2026-01",
        client_id="cid",
        client_secret="csecret",
    )
    shopify_client = ShopifyClient(config, http_client=client)  # type: ignore[arg-type]

    data = await shopify_client.execute("query { shop { name } }")

    assert data == {"shop": {"name": "Aayush"}}
    headers = client.graphql_calls[0][3]
    assert headers is not None and headers["X-Shopify-Access-Token"] == "tok_happy"
