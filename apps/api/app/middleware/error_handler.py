"""Centralized exception handling.

Maps domain exceptions (app.core.exceptions), FastAPI/Pydantic validation
errors, and unexpected exceptions to the standard ErrorResponse envelope.
Internal stack traces are never returned to the client — they are logged
server-side only.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import OMSError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _error_response(*, code: str, message: str, details: dict | None = None) -> dict:
    return {
        "success": False,
        "error": {"code": code, "message": message, "details": details or {}},
        "meta": {},
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(OMSError)
    async def oms_error_handler(request: Request, exc: OMSError) -> JSONResponse:
        logger.warning(
            "domain_error",
            path=request.url.path,
            error_code=exc.error_code,
            message=exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_response(code=exc.error_code, message=exc.message, details=exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # `exc.errors()` can contain a `ctx.error` entry that's the raw
        # exception object (e.g. a `ValueError` raised inside a
        # `@model_validator`), which `json.dumps`/`JSONResponse` can't
        # serialize as-is. Round-tripping through `json.dumps(...,
        # default=str)` stringifies exactly those non-serializable leaves
        # (turning `ValueError("...")` into its message text) and is a
        # no-op for every error shape that was already serializable.
        safe_errors = json.loads(json.dumps(exc.errors(), default=str))
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_response(
                code="validation_error",
                message="Request validation failed",
                details={"errors": safe_errors},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_response(code="http_error", message=str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            path=request.url.path,
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_response(
                code="internal_error",
                message="An unexpected error occurred. Please try again later.",
            ),
        )
