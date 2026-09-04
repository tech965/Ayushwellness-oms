"""The LLM <-> tool orchestration loop.

`ChatService.answer` runs one user turn to completion: it lets the model
call whitelisted tools (executed by `ToolRunner` against real OMS
services), feeds results back, and returns the model's final prose plus
the structured metadata the API and audit log need.

Anti-hallucination is enforced here, not left to the prompt: if the turn
required data but no tool call succeeded, the model's text is discarded
and replaced with a fixed "couldn't retrieve" message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.llm import ChatLLMError, GroqClient, LLMNotConfigured
from app.chat.prompts import build_system_prompt
from app.chat.tool_runner import ToolRunner
from app.chat.tools import TOOLS
from app.core.config import settings
from app.core.logging import get_logger
from app.models.auth import User

logger = get_logger(__name__)

_COULD_NOT_RETRIEVE = (
    "I couldn't retrieve the required OMS data right now. Please try again "
    "in a moment — if it keeps failing, the Shopify/Shiprocket sync or the "
    "AI service may be temporarily unavailable."
)
_NOT_CONFIGURED = (
    "The OMS AI Assistant isn't configured yet — an administrator needs to "
    "set the AI service API key. No data has been changed."
)


@dataclass
class ChatResult:
    answer: str
    ok: bool
    tools_used: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    partial: bool = False
    error_code: str | None = None
    model: str | None = None
    iterations: int = 0


class ChatService:
    def __init__(
        self,
        session: AsyncSession,
        user: User,
        *,
        llm: GroqClient | None = None,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.user = user
        self.now = now or datetime.now(UTC)
        self.llm = llm or GroqClient()
        self.runner = ToolRunner(session, user, now=self.now)

    async def answer(self, message: str, history: list[dict[str, str]] | None = None) -> ChatResult:
        if not settings.CHAT_ENABLED or not self.llm.configured:
            return ChatResult(answer=_NOT_CONFIGURED, ok=False, error_code="not_configured")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(self.now)}
        ]
        messages.extend(_sanitize_history(history))
        messages.append({"role": "user", "content": message.strip()})

        tools_used: list[str] = []
        sources: list[str] = []
        tool_data: dict[str, Any] = {}
        any_tool_attempted = False
        any_tool_ok = False
        any_tool_failed = False

        try:
            for iteration in range(1, settings.CHAT_MAX_TOOL_ITERATIONS + 1):
                reply = await self._complete(messages)
                tool_calls = reply.get("tool_calls") or []

                if not tool_calls:
                    final_text = (reply.get("content") or "").strip()
                    return self._finalize(
                        final_text,
                        tools_used=tools_used,
                        sources=sources,
                        tool_data=tool_data,
                        any_tool_attempted=any_tool_attempted,
                        any_tool_ok=any_tool_ok,
                        any_tool_failed=any_tool_failed,
                        iterations=iteration,
                    )

                # Echo the assistant's tool-call message back, then answer each call.
                messages.append(_assistant_tool_call_msg(reply, tool_calls))
                for call in tool_calls:
                    any_tool_attempted = True
                    name, args = _parse_tool_call(call)
                    outcome = await self.runner.run(name, args)
                    tools_used.append(name)
                    if outcome.ok:
                        any_tool_ok = True
                        for src in outcome.sources:
                            if src not in sources:
                                sources.append(src)
                        if outcome.data is not None:
                            tool_data[name] = outcome.data
                    else:
                        any_tool_failed = True
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or name,
                            "name": name,
                            "content": outcome.content,
                        }
                    )

            # Ran out of iterations — make one last, tool-free pass for a summary.
            reply = await self._complete(messages, allow_tools=False)
            return self._finalize(
                (reply.get("content") or "").strip(),
                tools_used=tools_used,
                sources=sources,
                tool_data=tool_data,
                any_tool_attempted=any_tool_attempted,
                any_tool_ok=any_tool_ok,
                any_tool_failed=any_tool_failed,
                iterations=settings.CHAT_MAX_TOOL_ITERATIONS,
            )

        except LLMNotConfigured:
            return ChatResult(answer=_NOT_CONFIGURED, ok=False, error_code="not_configured")
        except ChatLLMError as exc:
            logger.warning("chat_llm_error", error=str(exc), retryable=exc.retryable)
            return ChatResult(
                answer=_COULD_NOT_RETRIEVE if any_tool_ok is False else str(exc),
                ok=False,
                tools_used=tools_used,
                sources=sources,
                data=tool_data,
                partial=any_tool_ok,
                error_code="llm_error",
                model=self.llm.model,
            )

    async def _complete(
        self, messages: list[dict[str, Any]], *, allow_tools: bool = True
    ) -> dict[str, Any]:
        return await self.llm.chat_completion(
            messages,
            tools=TOOLS if allow_tools else None,
            tool_choice="auto" if allow_tools else "none",
        )

    def _finalize(
        self,
        final_text: str,
        *,
        tools_used: list[str],
        sources: list[str],
        tool_data: dict[str, Any],
        any_tool_attempted: bool,
        any_tool_ok: bool,
        any_tool_failed: bool,
        iterations: int,
    ) -> ChatResult:
        # Data was needed but nothing came back -> never ship the model's prose.
        if any_tool_attempted and not any_tool_ok:
            return ChatResult(
                answer=_COULD_NOT_RETRIEVE,
                ok=False,
                tools_used=tools_used,
                sources=sources,
                data=tool_data,
                partial=False,
                error_code="all_tools_failed",
                model=self.llm.model,
                iterations=iterations,
            )

        if not final_text:
            final_text = (
                "I don't have an answer for that. Try rephrasing, or ask about "
                "orders, revenue, payments, shipments, NDR/RTO, returns, "
                "products or courier performance for a given period."
            )

        return ChatResult(
            answer=final_text,
            ok=True,
            tools_used=tools_used,
            sources=sources,
            data=tool_data,
            # "partial": some data came back, but at least one tool also failed.
            partial=any_tool_ok and any_tool_failed,
            model=self.llm.model,
            iterations=iterations,
        )


def _sanitize_history(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    if not history:
        return []
    clean: list[dict[str, str]] = []
    for turn in history[-settings.CHAT_HISTORY_MAX_MESSAGES :]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            clean.append({"role": role, "content": content})
    return clean


def _assistant_tool_call_msg(
    reply: dict[str, Any], tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": reply.get("content") or "",
        "tool_calls": tool_calls,
    }


def _parse_tool_call(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    fn = call.get("function") or {}
    name = fn.get("name") or ""
    raw_args = fn.get("arguments")
    if isinstance(raw_args, dict):
        return name, raw_args
    if not raw_args:
        return name, {}
    try:
        parsed = json.loads(raw_args)
        return name, parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return name, {}
