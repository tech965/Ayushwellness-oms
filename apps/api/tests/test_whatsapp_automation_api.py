"""WhatsApp automation API: RBAC gating and basic CRUD round-trips."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_status_requires_permission(db_session: AsyncSession, make_authenticated_client):
    client = await make_authenticated_client(db_session, permission_codes=[])
    async with client:
        response = await client.get("/api/v1/whatsapp/status")
    assert response.status_code == 403


async def test_status_ok_with_read_permission(db_session: AsyncSession, make_authenticated_client):
    client = await make_authenticated_client(db_session, permission_codes=["integrations.read"])
    async with client:
        response = await client.get("/api/v1/whatsapp/status")
    assert response.status_code == 200
    assert response.json()["data"]["configured"] is False


async def test_create_contact_requires_manage_permission(
    db_session: AsyncSession, make_authenticated_client
):
    client = await make_authenticated_client(db_session, permission_codes=["integrations.read"])
    async with client:
        response = await client.post(
            "/api/v1/whatsapp/contacts", json={"name": "Alice", "phone": "9991234567"}
        )
    assert response.status_code == 403


async def test_create_contact_and_list(db_session: AsyncSession, make_authenticated_client):
    client = await make_authenticated_client(
        db_session, permission_codes=["integrations.manage", "integrations.read"]
    )
    async with client:
        create = await client.post(
            "/api/v1/whatsapp/contacts", json={"name": "Alice", "phone": "9991234567"}
        )
        assert create.status_code == 200
        assert create.json()["data"]["phone_number"] == "919991234567"

        listing = await client.get("/api/v1/whatsapp/contacts")
    assert listing.status_code == 200
    assert len(listing.json()["data"]) == 1


async def test_create_contact_rejects_invalid_phone(
    db_session: AsyncSession, make_authenticated_client
):
    client = await make_authenticated_client(db_session, permission_codes=["integrations.manage"])
    async with client:
        response = await client.post(
            "/api/v1/whatsapp/contacts", json={"name": "Bad", "phone": "123"}
        )
    assert response.status_code == 422


async def test_create_schedule_requires_recipients(
    db_session: AsyncSession, make_authenticated_client
):
    client = await make_authenticated_client(db_session, permission_codes=["integrations.manage"])
    async with client:
        response = await client.post(
            "/api/v1/whatsapp/schedules",
            json={
                "name": "Welcome",
                "schedule_kind": "one_time",
                "message_type": "text",
                "body_text": "Hi",
                "send_at": "2026-12-01T09:00:00Z",
                "recipients": [],
            },
        )
    assert response.status_code == 422


async def test_create_schedule_rejects_missing_body_for_text(
    db_session: AsyncSession, make_authenticated_client
):
    client = await make_authenticated_client(db_session, permission_codes=["integrations.manage"])
    async with client:
        contact = await client.post(
            "/api/v1/whatsapp/contacts", json={"name": "Alice", "phone": "9991234567"}
        )
        contact_id = contact.json()["data"]["id"]
        response = await client.post(
            "/api/v1/whatsapp/schedules",
            json={
                "name": "Welcome",
                "schedule_kind": "one_time",
                "message_type": "text",
                "send_at": "2026-12-01T09:00:00Z",
                "recipients": [{"contact_id": contact_id}],
            },
        )
    assert response.status_code == 422


async def test_full_schedule_lifecycle(db_session: AsyncSession, make_authenticated_client):
    client = await make_authenticated_client(
        db_session, permission_codes=["integrations.manage", "integrations.read"]
    )
    async with client:
        contact = await client.post(
            "/api/v1/whatsapp/contacts", json={"name": "Alice", "phone": "9991234567"}
        )
        contact_id = contact.json()["data"]["id"]

        create = await client.post(
            "/api/v1/whatsapp/schedules",
            json={
                "name": "Welcome",
                "schedule_kind": "one_time",
                "message_type": "text",
                "body_text": "Hi there",
                "send_at": "2026-12-01T09:00:00Z",
                "recipients": [{"contact_id": contact_id}],
            },
        )
        assert create.status_code == 200
        schedule = create.json()["data"]
        assert schedule["is_enabled"] is True
        assert schedule["recipient_count"] == 1
        schedule_id = schedule["id"]

        # Pause (ON/OFF toggle + pause/resume per the spec).
        paused = await client.patch(
            f"/api/v1/whatsapp/schedules/{schedule_id}", json={"is_paused": True}
        )
        assert paused.status_code == 200
        assert paused.json()["data"]["is_paused"] is True

        resumed = await client.patch(
            f"/api/v1/whatsapp/schedules/{schedule_id}", json={"is_paused": False}
        )
        assert resumed.json()["data"]["is_paused"] is False

        listing = await client.get("/api/v1/whatsapp/schedules")
        assert len(listing.json()["data"]) == 1

        deleted = await client.delete(f"/api/v1/whatsapp/schedules/{schedule_id}")
        assert deleted.status_code == 200

        after = await client.get("/api/v1/whatsapp/schedules")
    assert after.json()["data"] == []


async def test_dashboard_summary_shape(db_session: AsyncSession, make_authenticated_client):
    client = await make_authenticated_client(db_session, permission_codes=["integrations.read"])
    async with client:
        response = await client.get("/api/v1/whatsapp/dashboard")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["active_schedules"] == 0
    assert data["latest_daily_report_status"] == "never run"
