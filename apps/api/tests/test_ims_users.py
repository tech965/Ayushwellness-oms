"""IMS user management + access control, using the real seeded role definitions."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from app.models.audit_log import AuditLog
from app.models.rbac import Role
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SEED = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "seed.py"


async def _seed_roles(session: AsyncSession) -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("seed_script", _SEED)
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    perms = await seed._seed_permissions(session)
    await seed._seed_roles(session, perms)
    await session.commit()
    rows = (await session.execute(select(Role))).scalars().all()
    return {r.name: str(r.id) for r in rows}


async def _login(email: str, password: str) -> AsyncClient:
    from app.main import app

    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await anon.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["data"]["access_token"]
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _anon_login_status(email: str, password: str) -> int:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return (await c.post("/api/v1/auth/login", json={"email": email, "password": password})).status_code


async def _setup(db_session, make_authenticated_client):
    roles = await _seed_roles(db_session)
    admin = await make_authenticated_client(
        db_session, permission_codes=[], is_superuser=True, email="admin@x.com"
    )

    async def create(name, email, role):
        return await admin.post(
            "/api/v1/users",
            json={"name": name, "email": email, "phone": "9999999999", "password": "Passw0rd!x",
                  "role_ids": [roles[role]]},
        )

    return admin, roles, create


async def test_admin_creates_ims_users_and_duplicates_are_rejected(db_session, make_authenticated_client):
    admin, roles, create = await _setup(db_session, make_authenticated_client)
    s = await create("Staff One", "staff@x.com", "INVENTORY_STAFF")
    m = await create("Manager One", "manager@x.com", "INVENTORY_MANAGER")
    assert s.status_code == m.status_code == 201
    assert s.json()["data"]["roles"] == ["INVENTORY_STAFF"]
    assert m.json()["data"]["roles"] == ["INVENTORY_MANAGER"]
    assert "password" not in s.text and "password_hash" not in s.text
    # duplicate email, also differing only by case
    assert (await create("Dup", "staff@x.com", "INVENTORY_STAFF")).status_code == 409
    assert (await create("Dup", "STAFF@x.com", "INVENTORY_STAFF")).status_code == 409
    # audit log recorded, without secrets
    logs = (await db_session.execute(select(AuditLog).where(AuditLog.action == "user.created"))).scalars().all()
    assert len(logs) == 2
    assert all("Passw0rd" not in str(l.new_value) for l in logs)


async def test_staff_and_manager_permissions(db_session, make_authenticated_client):
    admin, roles, create = await _setup(db_session, make_authenticated_client)
    await create("Staff", "staff@x.com", "INVENTORY_STAFF")
    await create("Manager", "manager@x.com", "INVENTORY_MANAGER")

    staff = await _login("staff@x.com", "Passw0rd!x")
    assert (await staff.get("/api/v1/ims/stock")).status_code == 200
    assert (await staff.get("/api/v1/ims/dashboard")).status_code in (200, 502)
    # staff cannot reach management settings
    assert (await staff.post("/api/v1/ims/warehouses", json={"code": "9", "name": "X"})).status_code == 403
    assert (await staff.post("/api/v1/ims/packaging", json={"name": "P"})).status_code == 403
    assert (await staff.post("/api/v1/ims/catalog/refresh")).status_code == 403
    assert (await staff.post("/api/v1/ims/parties", json={"name": "Acme"})).status_code == 201  # write perm

    manager = await _login("manager@x.com", "Passw0rd!x")
    wh = await manager.post("/api/v1/ims/warehouses", json={"code": "9", "name": "Annex"})
    assert wh.status_code == 201
    assert (await manager.post("/api/v1/ims/packaging", json={"name": "P", "units": ["BOX"]})).status_code == 201

    # neither can touch unrelated OMS modules / admin APIs by URL
    for client in (staff, manager):
        for path in ("/api/v1/orders", "/api/v1/users", "/api/v1/roles", "/api/v1/customers",
                     "/api/v1/shipments", "/api/v1/audit-logs", "/api/v1/inventory/stock"):
            assert (await client.get(path)).status_code == 403, path
        assert (await client.put("/api/v1/settings", json={"general": {}})).status_code in (403, 405)
        assert (await client.post("/api/v1/users", json={"name": "x", "email": "a@b.co", "password": "Passw0rd!x"})).status_code == 403


async def test_inactive_ims_user_cannot_login_or_use_existing_token(db_session, make_authenticated_client):
    admin, roles, create = await _setup(db_session, make_authenticated_client)
    created = (await create("Staff", "staff@x.com", "INVENTORY_STAFF")).json()["data"]
    staff = await _login("staff@x.com", "Passw0rd!x")
    assert (await staff.get("/api/v1/ims/stock")).status_code == 200

    r = await admin.patch(f"/api/v1/users/{created['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["data"]["is_active"] is False
    assert await _anon_login_status("staff@x.com", "Passw0rd!x") == 401
    assert (await staff.get("/api/v1/ims/stock")).status_code == 401  # live token rejected

    await admin.patch(f"/api/v1/users/{created['id']}", json={"is_active": True})
    assert await _anon_login_status("staff@x.com", "Passw0rd!x") == 200


async def test_role_change_and_password_reset(db_session, make_authenticated_client):
    admin, roles, create = await _setup(db_session, make_authenticated_client)
    u = (await create("Staff", "staff@x.com", "INVENTORY_STAFF")).json()["data"]
    staff = await _login("staff@x.com", "Passw0rd!x")
    assert (await staff.post("/api/v1/ims/warehouses", json={"code": "9", "name": "X"})).status_code == 403

    await admin.patch(f"/api/v1/users/{u['id']}", json={"role_ids": [roles["INVENTORY_MANAGER"]]})
    manager = await _login("staff@x.com", "Passw0rd!x")
    assert (await manager.post("/api/v1/ims/warehouses", json={"code": "9", "name": "X"})).status_code == 201

    short = await admin.post(f"/api/v1/users/{u['id']}/reset-password", json={"new_password": "short"})
    assert short.status_code == 422
    ok = await admin.post(f"/api/v1/users/{u['id']}/reset-password", json={"new_password": "NewPassw0rd!y"})
    assert ok.status_code == 200
    assert await _anon_login_status("staff@x.com", "Passw0rd!x") == 401
    assert await _anon_login_status("staff@x.com", "NewPassw0rd!y") == 200
    actions = {l.action for l in (await db_session.execute(select(AuditLog))).scalars()}
    assert {"user.created", "user.updated", "user.password_reset"} <= actions
    dump = " ".join(str(l.new_value) for l in (await db_session.execute(select(AuditLog))).scalars())
    assert "Passw0rd" not in dump

    # a non-admin cannot reset passwords
    assert (await manager.post(f"/api/v1/users/{u['id']}/reset-password", json={"new_password": "Another1234!"})).status_code == 403


async def test_existing_roles_unaffected(db_session, make_authenticated_client):
    admin, roles, create = await _setup(db_session, make_authenticated_client)
    ops = await admin.post(
        "/api/v1/users",
        json={"name": "Ops", "email": "ops@x.com", "password": "Passw0rd!x", "role_ids": [roles["OPERATIONS"]]},
    )
    assert ops.status_code == 201
    client = await _login("ops@x.com", "Passw0rd!x")
    assert (await client.get("/api/v1/orders")).status_code == 200
    assert (await client.get("/api/v1/ims/stock")).status_code == 403  # no IMS permission
    me = (await client.get("/api/v1/auth/me")).json()["data"]
    assert "OPERATIONS" in me["roles"]


async def test_ims_user_role_creation_permissions_and_isolation(db_session, make_authenticated_client):
    admin, roles, _ = await _setup(db_session, make_authenticated_client)
    assert "IMS_USER" in roles
    role = (await db_session.execute(select(Role).where(Role.name == "IMS_USER"))).scalar_one()
    assert role.description == "IMS User role"

    created = await admin.post(
        "/api/v1/users",
        json={"name": "Ims Person", "email": "ims.user@x.com", "password": "Passw0rd!x",
              "role_ids": [roles["IMS_USER"]]},
    )
    assert created.status_code == 201
    assert created.json()["data"]["roles"] == ["IMS_USER"]

    client = await _login("ims.user@x.com", "Passw0rd!x")
    me = (await client.get("/api/v1/auth/me")).json()["data"]
    assert me["roles"] == ["IMS_USER"]
    assert sorted(me["permissions"]) == ["ims.read", "ims.stock.write"]

    # works in the IMS (read + stock operations), but not management or OMS
    assert (await client.get("/api/v1/ims/stock")).status_code == 200
    assert (await client.post("/api/v1/ims/parties", json={"name": "Acme"})).status_code == 201
    assert (await client.post("/api/v1/ims/warehouses", json={"code": "7", "name": "X"})).status_code == 403
    for path in ("/api/v1/orders", "/api/v1/users", "/api/v1/roles", "/api/v1/customers"):
        assert (await client.get(path)).status_code == 403, path
    # still works after a "refresh" (new login/session)
    again = await _login("ims.user@x.com", "Passw0rd!x")
    assert (await again.get("/api/v1/ims/stock")).status_code == 200
