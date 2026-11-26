"""Shiprocket shipping expenses and COD remittances.

Permissions follow the data: expense views need `analytics.read` (as the
Shiprocket Wallet page does), remittance views `reconciliation.read`, report
imports `reconciliation.upload`, and calling Shiprocket
`reconciliation.manage`. Nothing here writes to Shiprocket.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.remittance_reconciliation import (
    _column_map,
    _preview,
    _read_upload,
    _template,
)
from app.core.exceptions import IntegrationError
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.models.auth import User
from app.models.integration import IntegrationCode
from app.schemas.common import PageParams, build_pagination_meta
from app.schemas.response import ApiResponse
from app.services.remittance_common import paginate
from app.services.shiprocket_finance_service import (
    MAX_ORDERS_PER_SYNC,
    ShiprocketFinanceService,
)

router = APIRouter()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _expense_filters(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=128),
    courier: str | None = Query(default=None, max_length=128),
    txn_type: str | None = Query(default=None, max_length=32),
    status: str | None = Query(default=None, max_length=16),
) -> dict[str, Any]:
    return {"date_from": date_from, "date_to": date_to, "q": q, "courier": courier,
            "txn_type": txn_type, "status": status}  # fmt: skip


def _payout_filters(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=128),
    status: str | None = Query(default=None, max_length=64),
    bank_state: str | None = Query(default=None, max_length=24),
) -> dict[str, Any]:
    return {"date_from": date_from, "date_to": date_to, "q": q, "status": status,
            "bank_state": bank_state}  # fmt: skip


def _paged(rows: list, total: int, page_params: PageParams, **extra: Any) -> dict[str, Any]:
    return {
        "success": True,
        "data": rows,
        "meta": build_pagination_meta(total_items=total, page_params=page_params),
        **extra,
    }


def _file(content: bytes, name: str) -> Response:
    return Response(
        content=content,
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ------------------------------------------------------------------ expenses


@router.get("/expenses/sources", response_model=ApiResponse[list[dict]])
async def expense_sources(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[list[dict]]:
    return ApiResponse(data=await ShiprocketFinanceService(session).sources())


@router.get("/expenses/summary", response_model=ApiResponse[dict])
async def expense_summary(
    filters: dict = Depends(_expense_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[dict]:
    return ApiResponse(data=await ShiprocketFinanceService(session).expense_summary(filters))


@router.get("/expenses/records")
async def expense_records(
    kpi: str = Query(..., max_length=32),
    filters: dict = Depends(_expense_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> dict[str, Any]:
    rows, totals = await ShiprocketFinanceService(session).expense_drilldown(filters, kpi)
    page, total = paginate(rows, page_params)
    return _paged(page, total, page_params, totals=totals)


@router.get("/expenses/export")
async def expense_export(
    kpi: str = Query(..., max_length=32),
    filters: dict = Depends(_expense_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> Response:
    content = await ShiprocketFinanceService(session).export_expenses(filters, kpi)
    return _file(content, "shiprocket-expenses.xlsx")


@router.get("/expenses/shipment-charges")
async def shipment_charges(
    q: str | None = Query(default=None, max_length=128),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> dict[str, Any]:
    page, total, coverage = await ShiprocketFinanceService(session).charges(
        q=q, page_params=page_params
    )
    return _paged(page, total, page_params, coverage=coverage)


@router.post("/expenses/sync-order-finance", response_model=ApiResponse[dict])
async def sync_order_finance(
    limit: int = Query(default=10, ge=1, le=MAX_ORDERS_PER_SYNC),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.manage")),
) -> ApiResponse[dict]:
    """Reads up to `limit` orders from Shiprocket (one GET each) for their
    charge breakdown and COD UTR / status. Read-only toward Shiprocket."""
    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    if not isinstance(adapter, ShiprocketAdapter):
        raise IntegrationError("Shiprocket is not configured.")
    result = await ShiprocketFinanceService(session).sync_order_finance(
        adapter, limit=limit, user=user
    )
    return ApiResponse(data=result)


@router.post("/expenses/import/preview", response_model=ApiResponse[dict])
async def expense_import_preview(
    file: UploadFile = File(...),
    _: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[dict]:
    return ApiResponse(data=(await _preview("sr_wallet", file)).model_dump())


@router.get("/expenses/import/template")
async def expense_import_template(
    _: User = Depends(require_permission("reconciliation.upload")),
) -> Response:
    return _template("sr_wallet", "shiprocket-wallet-report-template.csv")


@router.post("/expenses/import", response_model=ApiResponse[dict], status_code=201)
async def expense_import(
    column_map: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[dict]:
    result = await ShiprocketFinanceService(session).import_wallet_report(
        filename=file.filename or "wallet.csv",
        content_type=file.content_type,
        data=await _read_upload(file),
        user=user,
        column_map=_column_map(column_map),
    )
    return ApiResponse(data=result)


# --------------------------------------------------------------- remittances


@router.get("/remittances/sources", response_model=ApiResponse[list[dict]])
async def remittance_sources(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[list[dict]]:
    return ApiResponse(data=await ShiprocketFinanceService(session).sources())


@router.get("/remittances/summary", response_model=ApiResponse[dict])
async def payout_summary(
    filters: dict = Depends(_payout_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[dict]:
    return ApiResponse(data=await ShiprocketFinanceService(session).payout_summary(filters))


@router.get("/remittances/records")
async def payout_records(
    kpi: str = Query(..., max_length=32),
    filters: dict = Depends(_payout_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> dict[str, Any]:
    rows, totals = await ShiprocketFinanceService(session).payout_drilldown(filters, kpi)
    page, total = paginate(rows, page_params)
    return _paged(page, total, page_params, totals=totals)


@router.get("/remittances/export")
async def payout_export(
    kpi: str = Query(..., max_length=32),
    filters: dict = Depends(_payout_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> Response:
    content = await ShiprocketFinanceService(session).export_payouts(filters, kpi)
    return _file(content, "shiprocket-remittances.xlsx")


@router.get("/remittances/order-status")
async def order_remittance_status(
    q: str | None = Query(default=None, max_length=128),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> dict[str, Any]:
    page, total = await ShiprocketFinanceService(session).order_remittance_status(
        q=q, page_params=page_params
    )
    return _paged(page, total, page_params)


@router.post("/remittances/import/preview", response_model=ApiResponse[dict])
async def payout_import_preview(
    file: UploadFile = File(...),
    _: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[dict]:
    return ApiResponse(data=(await _preview("sr_payout", file)).model_dump())


@router.get("/remittances/import/template")
async def payout_import_template(
    _: User = Depends(require_permission("reconciliation.upload")),
) -> Response:
    return _template("sr_payout", "shiprocket-payout-template.csv")


@router.post("/remittances/import", response_model=ApiResponse[dict], status_code=201)
async def payout_import(
    column_map: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[dict]:
    result = await ShiprocketFinanceService(session).import_payouts(
        filename=file.filename or "payouts.csv",
        content_type=file.content_type,
        data=await _read_upload(file),
        user=user,
        column_map=_column_map(column_map),
    )
    return ApiResponse(data=result)


@router.get("/remittances/{payout_id}", response_model=ApiResponse[dict])
async def payout_detail(
    payout_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[dict]:
    return ApiResponse(data=await ShiprocketFinanceService(session).payout_detail(payout_id))
