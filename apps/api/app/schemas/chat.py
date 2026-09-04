"""Request/response contract for `POST /api/v1/chat`.

Deliberately small and additive: the frontend sends a message plus
optional prior turns; the response carries the assistant's prose, a
machine-readable `data` block built from tool results, and provenance
(`sources`, `tools_used`, `data_freshness_hint`). No raw Shopify/DB rows
are ever returned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)
    conversation_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    answer: str
    ok: bool = True
    partial: bool = False
    tools_used: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    conversation_id: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    timestamp: datetime


class ChatSuggestion(BaseModel):
    label: str
    prompt: str


class ChatSuggestionsResponse(BaseModel):
    suggestions: list[ChatSuggestion]
