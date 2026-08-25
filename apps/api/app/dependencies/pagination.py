"""Reusable pagination/sorting query-param dependencies.

`PageParams`/`SortParams` (`app/schemas/common.py`) are plain Pydantic
models with a synthesized `**data` `__init__`, which FastAPI can't
introspect for individual query params via a bare `Depends()`. These
thin wrapper functions are what's actually declared on every list route,
so pagination/sorting query params are defined once (spec §37) instead of
per endpoint.
"""

from __future__ import annotations

from fastapi import Query

from app.schemas.common import PageParams, SortParams


def pagination_params(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


def sort_params(
    sort_by: str | None = Query(default=None),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> SortParams:
    return SortParams(sort_by=sort_by, sort_order=sort_order)
