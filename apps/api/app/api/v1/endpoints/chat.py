"""OMS AI Assistant endpoint.

`POST /api/v1/chat` — ask a question in natural language, get an answer
grounded in live OMS data. Requires the `chat.use` permission; individual
tools are further gated on the caller's existing module permissions
inside `ToolRunner`, so the assistant never widens a user's access.

Every call is recorded to `chat_query_logs` (question, answer, tools,
sources, latency, outcome) for internal audit — see `app.models.chat`.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.service import ChatService
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.models.chat import ChatQueryLog
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatSuggestion,
    ChatSuggestionsResponse,
)
from app.schemas.response import ApiResponse

logger = get_logger(__name__)
router = APIRouter()

_SUGGESTIONS: list[ChatSuggestion] = [
    ChatSuggestion(label="Today's orders", prompt="How many orders did we receive today?"),
    ChatSuggestion(label="Today's revenue", prompt="What is today's revenue?"),
    ChatSuggestion(label="COD vs prepaid", prompt="What is our COD vs prepaid split today?"),
    ChatSuggestion(label="Top products today", prompt="Give me today's top 5 products."),
    ChatSuggestion(
        label="Pending shipments",
        prompt="How many orders are pending fulfillment right now?",
    ),
    ChatSuggestion(label="RTO summary", prompt="What is our RTO count this month?"),
    ChatSuggestion(label="NDR summary", prompt="How many open NDR orders do we have?"),
    ChatSuggestion(
        label="Compare today vs yesterday",
        prompt="Compare today's orders and revenue with yesterday.",
    ),
    ChatSuggestion(
        label="Operations digest",
        prompt="Give me the most important problems I should look at today.",
    ),
]


@router.get("/suggestions", response_model=ApiResponse[ChatSuggestionsResponse])
async def chat_suggestions(
    _: User = Depends(require_permission("chat.use")),
) -> ApiResponse[ChatSuggestionsResponse]:
    return ApiResponse(data=ChatSuggestionsResponse(suggestions=_SUGGESTIONS))


@router.post("", response_model=ApiResponse[ChatResponse])
async def chat(
    payload: ChatRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("chat.use")),
) -> ApiResponse[ChatResponse]:
    started = time.monotonic()
    now = datetime.now(UTC)
    conversation_id = payload.conversation_id or uuid.uuid4().hex

    service = ChatService(session, current_user, now=now)
    result = await service.answer(
        payload.message,
        history=[turn.model_dump() for turn in payload.history],
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    await _record(
        session,
        user=current_user,
        conversation_id=conversation_id,
        question=payload.message,
        answer=result.answer,
        ok=result.ok,
        partial=result.partial,
        error_code=result.error_code,
        tools_used=result.tools_used,
        sources=result.sources,
        model=result.model or settings.CHAT_LLM_MODEL,
        latency_ms=latency_ms,
    )

    response = ChatResponse(
        answer=result.answer,
        ok=result.ok,
        partial=result.partial,
        tools_used=result.tools_used,
        sources=result.sources,
        data=result.data,
        error_code=result.error_code,
        conversation_id=conversation_id,
        model=result.model,
        latency_ms=latency_ms,
        timestamp=now,
    )
    message = (
        "Success" if result.ok else (result.error_code or "The assistant could not fully answer.")
    )
    return ApiResponse(data=response, success=result.ok, message=message)


async def _record(
    session: AsyncSession,
    *,
    user: User,
    conversation_id: str,
    question: str,
    answer: str,
    ok: bool,
    partial: bool,
    error_code: str | None,
    tools_used: list[str],
    sources: list[str],
    model: str,
    latency_ms: int,
) -> None:
    """Best-effort audit write — a logging failure must never break the
    user's answer."""
    try:
        session.add(
            ChatQueryLog(
                user_id=user.id,
                conversation_id=conversation_id,
                question=question[:8000],
                answer=(answer or "")[:8000],
                ok=ok,
                partial=partial,
                error_code=error_code,
                tools_used=tools_used or None,
                sources=sources or None,
                model=model,
                latency_ms=latency_ms,
            )
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("chat_query_log_write_failed", conversation_id=conversation_id)
        await session.rollback()
