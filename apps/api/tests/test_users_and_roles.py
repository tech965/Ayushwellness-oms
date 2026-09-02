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


async def test_list_users_serializes_roles_without_missing_greenlet(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Real production incident: `GET /users` 500'd with `MissingGreenlet`
    -- `UserService.list_users` -> `UserRepository.list` (the generic
    `BaseRepository.list`, driven by `UserRepository._base_query`) loaded
    plain `User` rows with no eager-load, and `_to_response()` then
    accessed `user.role_names` (`user.user_roles[].role`) on each one,
    triggering a lazy load AsyncSession can't service outside an active
    greenlet. This covers exactly the list endpoint the earlier
    create/update/get/delete tests above didn't.
    """
    async with await make_authenticated_client(
        db_session, permission_codes=["users.manage", "roles.manage"]
    ) as auth_client:
        role = await auth_client.post(
            "/api/v1/roles", json={"name": "LIST_TEST_ROLE", "permission_ids": []}
        )
        role_id = role.json()["data"]["id"]

        with_role = await auth_client.post(
            "/api/v1/users",
            json={
                "name": "Has Role",
                "email": "has-role@example.com",
                "password": "Sup3rSecret!",
                "role_ids": [role_id],
            },
        )
        assert with_role.status_code == 201

        without_role = await auth_client.post(
            "/api/v1/users",
            json={
                "name": "No Role",
                "email": "no-role@example.com",
                "password": "Sup3rSecret!",
                "role_ids": [],
            },
        )
        assert without_role.status_code == 201

        listed = await auth_client.get("/api/v1/users", params={"page": 1, "page_size": 50})
        assert listed.status_code == 200
        body = listed.json()
        assert body["success"] is True

        by_email = {u["email"]: u for u in body["data"]}
        assert by_email["has-role@example.com"]["roles"] == ["LIST_TEST_ROLE"]
        assert by_email["no-role@example.com"]["roles"] == []

        # Pagination still works: the auth_client's own user plus the two
        # created above is at least 3 rows total, and page_size=1 must cap
        # `data` at exactly one row while `meta` still reports the true total.
        meta = body["meta"]
        assert meta["total_items"] >= 3
        page_one = await auth_client.get("/api/v1/users", params={"page": 1, "page_size": 1})
        assert page_one.status_code == 200
        page_one_body = page_one.json()
        assert len(page_one_body["data"]) == 1
        assert page_one_body["meta"]["page"] == 1
        assert page_one_body["meta"]["page_size"] == 1
        assert page_one_body["meta"]["total_items"] == meta["total_items"]
        assert page_one_body["meta"]["total_pages"] == meta["total_items"]


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
