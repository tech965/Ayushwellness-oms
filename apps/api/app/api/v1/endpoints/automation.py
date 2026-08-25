"""automation endpoints.

Status: PLANNED — implemented in Phase 5.
Automation rule CRUD and execution history.

This router is registered now so the URL namespace and module boundary
are fixed from Phase 0 onward; endpoints are added when the corresponding
phase is implemented.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
