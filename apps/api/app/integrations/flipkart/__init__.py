"""Flipkart Seller API integration — Marketplace Orders module, Phase 1 (stub).

Registers a stub `FlipkartAdapter`: `health_check`/`authenticate` always
report "not configured", `fetch`/`fetch_incremental` always return an
empty page, and `process_webhook` raises `NotImplementedError`. Mirrors
`app.integrations.amazon`'s own stub exactly — see that package's
docstring for the full rationale, which applies here unchanged.
"""

from __future__ import annotations

from app.integrations.flipkart.adapter import FlipkartAdapter
from app.integrations.registry import register_adapter

__all__ = ["FlipkartAdapter", "register"]


def register() -> None:
    register_adapter(FlipkartAdapter())
