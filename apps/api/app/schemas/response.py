"""Standard API response envelope.

Every endpoint returns this shape so frontend clients can rely on a
single consistent contract:

    {"success": true, "data": {...}, "message": "Success", "meta": {}}

Errors use the same envelope via `ErrorResponse` (see
app.middleware.error_handler for the mapping from exceptions to this
shape).
"""

from __future__ import annotations

from pydantic import BaseModel


class ApiResponse[T](BaseModel):
    success: bool = True
    data: T | None = None
    message: str = "Success"
    meta: dict = {}


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    # Set only by live (API-first) listings, where the provider cannot
    # give an exact total. Absent/None everywhere else.
    has_more: bool | None = None
    total_is_estimate: bool | None = None
    warnings: list[str] | None = None


class PaginatedResponse[T](BaseModel):
    success: bool = True
    data: list[T]
    message: str = "Success"
    meta: PaginationMeta


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = {}


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
    meta: dict = {}
