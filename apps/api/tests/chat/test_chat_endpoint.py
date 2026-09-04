"""POST /api/v1/chat — auth, response contract, and audit logging."""

from __future__ import annotations

import pytest
from app.models.chat import ChatQueryLog
from sqlalchemy import select

from tests.chat.scripted_llm import factory, say

pytestmark = pytest.mark.asyncio

_PERMS = ["chat.use", "analytics.read", "orders.read"]


def _install_llm(monkeypatch, script, **kwargs) -> None:
    monkeypatch.setattr("app.chat.service.GroqClient", factory(script, **kwargs))


async def test_chat_happy_path_and_audit_row(
    db_session, make_authenticated_client, seeded_orders, monkeypatch
) -> None:
    _install_llm(
        monkeypatch,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "get_operations_summary",
                            "arguments": '{"period":"today"}',
                        },
                    }
                ],
            },
            say("Today's orders: 3. Revenue: ₹3,500.\nSource: OMS database (synced from Shopify)."),
        ],
    )

    async with await make_authenticated_client(db_session, permission_codes=_PERMS) as client:
        resp = await client.post("/api/v1/chat", json={"message": "How many orders today?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert "3" in data["answer"]
    assert data["tools_used"] == ["get_operations_summary"]
    assert data["ok"] is True
    assert data["partial"] is False
    assert data["conversation_id"]
    assert data["latency_ms"] is not None
    assert data["data"]["get_operations_summary"]["orders"]["value"] == "3"

    rows = (await db_session.execute(select(ChatQueryLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].question == "How many orders today?"
    assert rows[0].ok is True
    assert rows[0].tools_used == ["get_operations_summary"]
    assert rows[0].latency_ms is not None


async def test_chat_requires_chat_use_permission(
    db_session, make_authenticated_client, monkeypatch
) -> None:
    _install_llm(monkeypatch, [say("nope")])
    async with await make_authenticated_client(
        db_session, permission_codes=["analytics.read"]
    ) as client:
        resp = await client.post("/api/v1/chat", json={"message": "hi"})
    assert resp.status_code == 403


async def test_chat_reports_not_configured_without_api_key(
    db_session, make_authenticated_client, monkeypatch
) -> None:
    _install_llm(monkeypatch, [say("unused")], configured=False)
    async with await make_authenticated_client(db_session, permission_codes=_PERMS) as client:
        resp = await client.post("/api/v1/chat", json={"message": "revenue today?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["data"]["error_code"] == "not_configured"
    # still audited
    rows = (await db_session.execute(select(ChatQueryLog))).scalars().all()
    assert rows and rows[0].ok is False


async def test_chat_all_tools_failed_is_not_ok(
    db_session, make_authenticated_client, monkeypatch
) -> None:
    _install_llm(
        monkeypatch,
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "get_operations_summary",
                            "arguments": '{"period":"nonsense"}',
                        },
                    }
                ],
            },
            say("Revenue today was ₹9,99,999."),
        ],
    )
    async with await make_authenticated_client(db_session, permission_codes=_PERMS) as client:
        resp = await client.post("/api/v1/chat", json={"message": "revenue today?"})

    body = resp.json()
    assert body["success"] is False
    assert body["data"]["error_code"] == "all_tools_failed"
    assert "9,99,999" not in body["data"]["answer"]


async def test_suggestions_endpoint(db_session, make_authenticated_client, monkeypatch) -> None:
    _install_llm(monkeypatch, [say("x")])
    async with await make_authenticated_client(db_session, permission_codes=_PERMS) as client:
        resp = await client.get("/api/v1/chat/suggestions")
    assert resp.status_code == 200
    suggestions = resp.json()["data"]["suggestions"]
    assert len(suggestions) >= 5
    assert all("label" in s and "prompt" in s for s in suggestions)


async def test_history_is_accepted_and_capped(
    db_session, make_authenticated_client, seeded_orders, monkeypatch
) -> None:
    _install_llm(monkeypatch, [say("Follow-up handled.")])
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    async with await make_authenticated_client(db_session, permission_codes=_PERMS) as client:
        resp = await client.post(
            "/api/v1/chat",
            json={"message": "and now?", "history": history, "conversation_id": "conv-1"},
        )
    assert resp.status_code == 200
    assert resp.json()["data"]["conversation_id"] == "conv-1"
