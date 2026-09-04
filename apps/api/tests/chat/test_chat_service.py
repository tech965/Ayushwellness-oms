"""ChatService orchestration + the anti-hallucination guarantee."""

from __future__ import annotations

import pytest
from app.chat.llm import ChatLLMError
from app.chat.service import _COULD_NOT_RETRIEVE, _NOT_CONFIGURED, ChatService

from tests.chat.conftest import NOW
from tests.chat.scripted_llm import ScriptedLLM, call, say

pytestmark = pytest.mark.asyncio


async def test_one_tool_then_answer(db_session, chat_user, seeded_orders) -> None:
    llm = ScriptedLLM(
        [call("get_operations_summary", {"period": "today"}), say("Orders today: 3.")]
    )
    result = await ChatService(db_session, chat_user, llm=llm, now=NOW).answer("orders today?")

    assert result.ok
    assert result.answer == "Orders today: 3."
    assert result.tools_used == ["get_operations_summary"]
    assert result.data["get_operations_summary"]["orders"]["value"] == "3"
    assert any("OMS database" in s for s in result.sources)
    assert result.partial is False


async def test_not_configured_short_circuits(db_session, chat_user) -> None:
    llm = ScriptedLLM([say("should never be used")], configured=False)
    result = await ChatService(db_session, chat_user, llm=llm, now=NOW).answer("orders today?")
    assert not result.ok
    assert result.error_code == "not_configured"
    assert result.answer == _NOT_CONFIGURED
    assert llm.calls == []  # never called the model


async def test_all_tools_failed_replaces_model_text(db_session, chat_user) -> None:
    # The model calls a tool with a bad preset (guaranteed failure), then
    # tries to answer anyway with a fabricated number.
    llm = ScriptedLLM(
        [
            call("get_operations_summary", {"period": "not_a_real_preset"}),
            say("We did ₹5,00,000 in revenue today."),
        ]
    )
    result = await ChatService(db_session, chat_user, llm=llm, now=NOW).answer("revenue today?")

    assert not result.ok
    assert result.error_code == "all_tools_failed"
    assert result.answer == _COULD_NOT_RETRIEVE
    assert "5,00,000" not in result.answer


async def test_partial_when_one_of_two_tools_fails(db_session, chat_user, seeded_orders) -> None:
    llm = ScriptedLLM(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "a",
                        "type": "function",
                        "function": {
                            "name": "get_operations_summary",
                            "arguments": '{"period":"today"}',
                        },
                    },
                    {
                        "id": "b",
                        "type": "function",
                        "function": {"name": "get_top_products", "arguments": '{"period":"bogus"}'},
                    },
                ],
            },
            say("Orders today: 3. (Top products unavailable.)"),
        ]
    )
    result = await ChatService(db_session, chat_user, llm=llm, now=NOW).answer(
        "summary + top products"
    )
    assert result.ok
    assert result.partial is True
    assert result.tools_used == ["get_operations_summary", "get_top_products"]


async def test_no_tools_plain_answer_is_allowed(db_session, chat_user) -> None:
    llm = ScriptedLLM(
        [say("I can help with orders, revenue, shipments and more. What do you need?")]
    )
    result = await ChatService(db_session, chat_user, llm=llm, now=NOW).answer("hi")
    assert result.ok
    assert result.tools_used == []
    assert result.partial is False


async def test_llm_error_after_no_successful_tool_returns_safe_message(
    db_session, chat_user
) -> None:
    class Boom(ScriptedLLM):
        async def chat_completion(self, messages, **kwargs):  # noqa: ANN001
            raise ChatLLMError("boom", retryable=True)

    result = await ChatService(db_session, chat_user, llm=Boom([]), now=NOW).answer("orders today?")
    assert not result.ok
    assert result.error_code == "llm_error"
    assert result.answer == _COULD_NOT_RETRIEVE


async def test_iteration_cap_is_respected(
    db_session, chat_user, seeded_orders, monkeypatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "CHAT_MAX_TOOL_ITERATIONS", 2)
    # Model keeps asking for tools forever; after the cap we force a
    # tool-free summarizing pass.
    llm = ScriptedLLM(
        [
            call("get_operations_summary", {"period": "today"}),
            call("get_operations_summary", {"period": "yesterday"}),
            # 3rd model turn is the forced tool-free summarizing pass.
            say("Final."),
        ]
    )
    result = await ChatService(db_session, chat_user, llm=llm, now=NOW).answer("loop please")
    assert result.answer == "Final."
    assert result.iterations == 2
    assert len(llm.calls) == 3
