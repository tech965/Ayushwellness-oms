"""Meesho integration — Marketplace Orders module, Phase 1 (stub).

Registers a stub `MeeshoAdapter`, same shape as `app.integrations.amazon`/
`flipkart`. IMPORTANT (explicit requirement from the review meeting,
2026-11-05): Meesho does not have a confirmed, generally-available public
seller API the way Amazon (SP-API) and Flipkart (Seller API) do. This
stub exists so Meesho has the same navigation/dashboard/integration-list
presence as the other two marketplaces, but a real sync implementation
must only be built once Meesho actually grants official, authorized API
access — never scraped or reverse-engineered. Until then this adapter
stays permanently "not configured."
"""

from __future__ import annotations

from app.integrations.meesho.adapter import MeeshoAdapter
from app.integrations.registry import register_adapter

__all__ = ["MeeshoAdapter", "register"]


def register() -> None:
    register_adapter(MeeshoAdapter())
