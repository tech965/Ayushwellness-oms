from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_get_settings_returns_defaults_on_a_fresh_database(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    # GET only requires authentication -- no specific permission, matching
    # every role reading these values in the background (see
    # app/api/v1/endpoints/settings.py's docstring).
    async with await make_authenticated_client(
        db_session, permission_codes=["orders.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/settings")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["settings"]["general"]["organization_name"] == "AyushWellness"
        assert data["settings"]["general"]["default_page_size"] == 20
        assert data["settings"]["dashboard"]["refresh_interval_seconds"] == 0
        assert data["updated_by_email"] is None


async def test_update_settings_persists_one_section_without_touching_others(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["settings.manage"]
    ) as auth_client:
        updated = await auth_client.put(
            "/api/v1/settings",
            json={
                "general": {
                    "organization_name": "Ayush Wellness Pvt Ltd",
                    "oms_display_name": "Ayush OMS",
                    "default_timezone": "Asia/Kolkata",
                    "currency": "INR",
                    "date_format": "DD/MM/YYYY",
                    "default_page_size": 50,
                }
            },
        )
        assert updated.status_code == 200
        data = updated.json()["data"]
        assert data["settings"]["general"]["organization_name"] == "Ayush Wellness Pvt Ltd"
        assert data["settings"]["general"]["default_page_size"] == 50
        # Untouched section keeps its default.
        assert data["settings"]["dashboard"]["default_date_range"] == "last_30_days"
        assert data["updated_by_email"] == "user@example.com"

        # Persists across a fresh GET (separate request).
        refetched = await auth_client.get("/api/v1/settings")
        assert refetched.json()["data"]["settings"]["general"]["default_page_size"] == 50

        # A second update to a different section doesn't clobber the first.
        second = await auth_client.put(
            "/api/v1/settings",
            json={"dashboard": {"refresh_interval_seconds": 120}},
        )
        assert second.status_code == 200
        second_data = second.json()["data"]["settings"]
        assert second_data["dashboard"]["refresh_interval_seconds"] == 120
        assert second_data["general"]["default_page_size"] == 50


async def test_update_settings_requires_settings_manage_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["orders.read"]
    ) as auth_client:
        response = await auth_client.put(
            "/api/v1/settings",
            json={
                "general": {
                    "organization_name": "Nope",
                    "oms_display_name": "Nope OMS",
                    "default_timezone": "Asia/Kolkata",
                    "currency": "INR",
                    "date_format": "DD MMM YYYY",
                    "default_page_size": 20,
                }
            },
        )
        assert response.status_code == 403


async def test_settings_requires_authentication(db_session: AsyncSession, client) -> None:
    response = await client.get("/api/v1/settings")
    assert response.status_code == 401
