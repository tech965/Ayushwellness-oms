"""Shared request schemas (pagination, sorting, filtering)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.schemas.response import PaginationMeta


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class SortParams(BaseModel):
    sort_by: str | None = None
    sort_order: str = Field(default="desc", pattern="^(asc|desc)$")


def build_pagination_meta(*, total_items: int, page_params: PageParams) -> PaginationMeta:
    from app.schemas.response import PaginationMeta

    total_pages = (
        (total_items + page_params.page_size - 1) // page_params.page_size if total_items else 0
    )
    return PaginationMeta(
        page=page_params.page,
        page_size=page_params.page_size,
        total_items=total_items,
        total_pages=total_pages,
    )
