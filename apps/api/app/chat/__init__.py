"""OMS AI Assistant — a natural-language layer over the existing OMS.

Architecture (kept deliberately separate from the rest of `app/` so it
can be developed and tested in isolation before it's surfaced in the
dashboard nav):

    endpoints/chat.py        auth + audit + HTTP contract
        -> chat.service.ChatService      LLM <-> tool orchestration loop
              -> chat.llm.GroqClient     OpenAI-compatible Chat Completions
              -> chat.tools              tool JSON schemas the model sees
              -> chat.tool_runner        dispatch a tool call to a real
                                         OMS service (Analytics/Orders/...)
              -> chat.datetime_ranges    "today"/"last week"/... -> UTC,
                                         resolved in Asia/Kolkata
              -> chat.formatting         INR / Indian-digit-grouping output

Hard rules enforced here, not left to the model:
  * The model never touches the database or Shopify. It may only call the
    whitelisted tools in `chat.tools`, each of which runs an existing,
    already-tested OMS service method.
  * Every tool result carries its own data + source. If no tool
    succeeded, the endpoint returns a fixed "couldn't retrieve" message
    instead of whatever the model wrote — see `ChatService.answer`.
  * Tool access is re-checked against the caller's RBAC permissions in
    `tool_runner`, so the assistant can never widen what a user can see.
"""

from __future__ import annotations
