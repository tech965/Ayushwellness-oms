"""Minimal client for Groq's OpenAI-compatible Chat Completions API.

Only the surface the assistant needs: one `chat_completion` call with
tool definitions, returning the raw `choices[0].message` dict (which may
contain `tool_calls`). No streaming, no SDK dependency — the backend
already ships `httpx`.

Transient failures (HTTP 429 rate-limit, 5xx, timeout, connection error)
are retried here with backoff — Groq's per-minute token limit is easy to
brush against on a burst of tool round-trips, and a short wait clears it.
The `retry-after` / `x-ratelimit-reset-tokens` response headers are
honoured when present.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_RETRIES = 3
_BACKOFF_CAP_SECONDS = 20.0


class ChatLLMError(RuntimeError):
    """Any failure talking to the model provider. `retryable` hints whether
    a later attempt might succeed (timeout / 429 / 5xx)."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMNotConfigured(ChatLLMError):
    def __init__(self) -> None:
        super().__init__("The AI assistant is not configured (missing GROQ_API_KEY).")


class GroqClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.GROQ_API_KEY
        self._base_url = (base_url or settings.GROQ_API_BASE).rstrip("/")
        self._model = model or settings.CHAT_LLM_MODEL
        self._timeout = timeout or settings.CHAT_LLM_TIMEOUT_SECONDS
        self._client = http_client
        self._owns_client = http_client is None
        self._max_retries = max_retries

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    @property
    def model(self) -> str:
        return self._model

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        if not self.configured:
            raise LLMNotConfigured()

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        client = await self._get_client()
        last_error: ChatLLMError | None = None

        for attempt in range(self._max_retries + 1):
            try:
                return await self._attempt(client, payload)
            except ChatLLMError as exc:
                last_error = exc
                if not exc.retryable or attempt == self._max_retries:
                    raise
                delay = min(
                    getattr(exc, "retry_after", None) or _backoff(attempt),
                    _BACKOFF_CAP_SECONDS,
                )
                logger.warning(
                    "chat_llm_retrying",
                    attempt=attempt + 1,
                    delay_seconds=round(delay, 2),
                    reason=str(exc),
                )
                await asyncio.sleep(delay)

        assert last_error is not None  # loop always sets it before this point
        raise last_error

    async def _attempt(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            raise ChatLLMError("The AI service timed out.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ChatLLMError("Could not reach the AI service.", retryable=True) from exc

        if response.status_code == 401:
            raise ChatLLMError("The AI service rejected the API credentials.")
        if response.status_code == 429:
            err = ChatLLMError("The AI service is rate-limiting requests.", retryable=True)
            err.retry_after = _reset_hint(response)  # type: ignore[attr-defined]
            raise err
        if response.status_code >= 500:
            raise ChatLLMError("The AI service is temporarily unavailable.", retryable=True)
        if response.status_code >= 400:
            detail = _safe_error_detail(response)
            logger.warning("chat_llm_client_error", status=response.status_code, detail=detail)
            raise ChatLLMError("The AI service could not process the request.")

        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise ChatLLMError("The AI service returned an empty response.")
        return choices[0]["message"]


def _backoff(attempt: int) -> float:
    return 1.5 * (2**attempt)


def _reset_hint(response: httpx.Response) -> float | None:
    """Seconds to wait, from `retry-after` or Groq's `x-ratelimit-reset-*`
    headers (e.g. `1m26.4s`, `622ms`, `13`)."""
    for header in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        raw = response.headers.get(header)
        if not raw:
            continue
        seconds = _parse_duration(raw)
        if seconds is not None:
            return seconds
    return None


def _parse_duration(raw: str) -> float | None:
    raw = raw.strip()
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)
    total = 0.0
    matched = False
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)", raw):
        matched = True
        v = float(value)
        total += {"ms": v / 1000, "s": v, "m": v * 60, "h": v * 3600}[unit]
    return total if matched else None


def _safe_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        return str(data.get("error", {}).get("message") or data)[:500]
    except Exception:  # noqa: BLE001
        return response.text[:500]
