"""analytics endpoints.

Status: PLANNED — implemented in Phase 3 basic, Phase 4/6 advanced.
Basic analytics now; courier/city/PIN + ML-driven analytics later.

This router is registered now so the URL namespace and module boundary
are fixed from Phase 0 onward; endpoints are added when the corresponding
phase is implemented.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
