"""A stand-in for `GroqClient` that replays canned model responses.

Lets the orchestration loop, tool dispatch, and anti-hallucination guard
be tested without a network call. Each script entry is either a final
answer (`say(...)`) or a tool-call turn (`call(...)`).
"""

from __future__ import annotations

import json
from typing import Any


def say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def call(
    name: str, arguments: dict[str, Any] | None = None, *, call_id: str = "c1"
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments or {})},
            }
        ],
    }


class ScriptedLLM:
    def __init__(
        self, script: list[dict[str, Any]], *, configured: bool = True, model: str = "scripted"
    ) -> None:
        self._script = list(script)
        self._configured = configured
        self._model = model
        self.calls: list[list[dict[str, Any]]] = []

    # --- GroqClient surface used by ChatService ---
    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def model(self) -> str:
        return self._model

    async def chat_completion(
        self, messages: list[dict[str, Any]], **_kwargs: Any
    ) -> dict[str, Any]:
        self.calls.append(messages)
        if not self._script:
            return say("(scripted LLM ran out of responses)")
        return self._script.pop(0)

    async def aclose(self) -> None:  # pragma: no cover
        return None


def factory(script: list[dict[str, Any]], **kwargs: Any):
    """Returns a callable suitable for monkeypatching `app.chat.service.GroqClient`."""

    def _make(*_a: Any, **_k: Any) -> ScriptedLLM:
        return ScriptedLLM(list(script), **kwargs)

    return _make
