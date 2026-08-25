"""Regression coverage for role/user management, including the
create-then-update-permissions / create-then-update-roles paths that
originally hit a MissingGreenlet bug (touching a lazy relationship
collection under AsyncSession — see app/services/rbac_service.py and
app/services/user_service.py).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_role_create_then_update_permissions_twice(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["roles.manage"]
    ) as auth_client:
        perms = await auth_client.get("/api/v1/permissions")
        assert perms.status_code == 200
        # roles.manage always exists (it's what authorized this request).
        codes = [p["code"] for p in perms.json()["data"]]
        assert "roles.manage" in codes
        permission_id = next(p["id"] for p in perms.json()["data"] if p["code"] == "roles.manage")

        created = await auth_client.post(
            "/api/v1/roles", json={"name": "TEST_ROLE", "permission_ids": [permission_id]}
        )
        assert created.status_code == 201
        role_id = created.json()["data"]["id"]
        assert created.json()["data"]["permissions"] == ["roles.manage"]

        # Update permissions on the just-created role (this is exactly the
        # path that used to raise MissingGreenlet).
        updated = await auth_client.patch(f"/api/v1/roles/{role_id}", json={"permission_ids": []})
        assert updated.status_code == 200
        assert updated.json()["data"]["permissions"] == []

        get_response = await auth_client.get(f"/api/v1/roles/{role_id}")
        assert get_response.status_code == 200


async def test_user_create_then_update_roles_twice(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["users.manage", "roles.manage"]
    ) as auth_client:
        role = await auth_client.post(
            "/api/v1/roles", json={"name": "SUPPORT_TEST", "permission_ids": []}
        )
        role_id = role.json()["data"]["id"]

        created = await auth_client.post(
            "/api/v1/users",
            json={
                "name": "New Ops User",
                "email": "newops@example.com",
                "password": "Sup3rSecret!",
                "role_ids": [role_id],
            },
        )
        assert created.status_code == 201
        user_id = created.json()["data"]["id"]
        assert created.json()["data"]["roles"] == ["SUPPORT_TEST"]
        assert "password" not in created.text
        assert "password_hash" not in created.text

        # Update roles on the just-created user (this is exactly the path
        # that used to raise MissingGreenlet).
        updated = await auth_client.patch(f"/api/v1/users/{user_id}", json={"role_ids": []})
        assert updated.status_code == 200
        assert updated.json()["data"]["roles"] == []

        deactivated = await auth_client.delete(f"/api/v1/users/{user_id}")
        assert deactivated.status_code == 200

        final = await auth_client.get(f"/api/v1/users/{user_id}")
        assert final.json()["data"]["is_active"] is False
