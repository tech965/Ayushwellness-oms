"""Liveness and readiness probes.

GET /health       — liveness: process is up. No dependency checks.
GET /health/ready  — readiness: checks database and Redis connectivity.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.health import check_database, check_redis
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.APP_NAME, "environment": settings.ENVIRONMENT}


@router.get("/health/ready")
async def health_ready(session: AsyncSession = Depends(get_db)) -> JSONResponse:
    db_ok = await check_database(session)
    redis_ok = await check_redis()
    checks = {
        "database": "ok" if db_ok else "unavailable",
        "redis": "ok" if redis_ok else "unavailable",
    }
    all_ok = db_ok and redis_ok

    return JSONResponse(
        status_code=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if all_ok else "not_ready", "checks": checks},
    )
