"""Centralized error handling tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_unknown_route_returns_standard_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]


# Round 6 — production incident: a real HTTP 500 with a real JSON body
# carried NO `Access-Control-Allow-Origin` header (confirmed live), so
# the browser blocked the frontend's JS from ever seeing it — axios
# reported it as a plain "Network Error" instead of the actual error.
# Root cause: `@app.exception_handler(Exception)` is wired into
# Starlette's `ServerErrorMiddleware`, which sits *outside*
# `CORSMiddleware` regardless of `add_middleware` call order, so a
# response it builds never passes back through CORS handling. Fixed by
# `UnhandledExceptionMiddleware`, added innermost (before
# `CORSMiddleware`) in `main.py` so its response *is* an ordinary
# middleware return value that flows back out through CORS like any
# other response.
async def test_an_unhandled_exception_still_carries_cors_headers(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import auth_service

    async def _boom(self, *, email: str, password: str):  # noqa: ANN001, ARG001
        raise RuntimeError("simulated unhandled exception (e.g. a DB schema mismatch)")

    monkeypatch.setattr(auth_service.AuthService, "login", _boom)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "whatever"},
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "internal_error"
    # The exact bug: this header must be present on a 500 exactly as it
    # would be on any other response, or the browser hides the real
    # error from the frontend entirely.
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


async def test_a_normal_response_still_carries_cors_headers(client: AsyncClient) -> None:
    """Regression guard the other direction: the new middleware must not
    accidentally suppress CORS headers (or anything else) on a request
    that completes normally.
    """
    response = await client.get(
        "/api/v1/does-not-exist", headers={"Origin": "http://localhost:3000"}
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
