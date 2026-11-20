"""COD/Prepaid revenue split and Shiprocket COD remittance reconciliation.

Reads the existing local orders/shipments plus `cod_remittances` (synced
from the existing Shiprocket client). Not available in live-data mode.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthorizationError, IntegrationError
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.pagination import pagination_params
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.models.auth import User
from app.models.integration import IntegrationCode
from app.schemas.common import PageParams, build_pagination_meta
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services.cod_reconciliation_service import (
    CodReconciliationService,
    OrderRow,
    RevenueSummary,
)

router = APIRouter()


def _filters(
    payment_type: Literal["cod", "prepaid"] = Query(...),
    fulfillment: Literal["fulfilled", "unfulfilled", "partial"] | None = Query(default=None),
    q: str | None = Query(default=None, description="Order no., customer, phone, email or AWB"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> dict:
    return {
        "payment_type": payment_type,
        "fulfillment_status": fulfillment,
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
    }


@router.get("/summary", response_model=ApiResponse[RevenueSummary])
async def revenue_summary(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[RevenueSummary]:
    data = await CodReconciliationService(session).summary(date_from=date_from, date_to=date_to)
    return ApiResponse(data=data)


@router.get("/orders", response_model=PaginatedResponse[OrderRow])
async def revenue_orders(
    filters: dict = Depends(_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> PaginatedResponse[OrderRow]:
    rows, total, warnings = await CodReconciliationService(session).list_orders_with_status(
        page=page_params.page, page_size=page_params.page_size, **filters
    )
    meta = build_pagination_meta(total_items=total, page_params=page_params)
    meta.warnings = warnings or None
    return PaginatedResponse(data=rows, meta=meta)


@router.get("/orders/export")
async def export_revenue_orders(
    filters: dict = Depends(_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> StreamingResponse:
    workbook = await CodReconciliationService(session).export_xlsx(**filters)
    name = f"{filters['payment_type']}-revenue-reconciliation.xlsx"
    return StreamingResponse(
        iter([workbook]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )


def _shape(value: object, depth: int = 0) -> object:
    """Key names and value TYPES only -- never values (no PII/secrets)."""
    if isinstance(value, dict):
        if depth >= 3:
            return "object"
        return {k: _shape(v, depth + 1) for k, v in list(value.items())[:60]}
    if isinstance(value, list):
        return [f"list[{len(value)}]", _shape(value[0], depth + 1)] if value else "list[0]"
    return type(value).__name__ if value is not None else "null"


PROBE_PATHS = (
    "/account/details/statement",
    "/account/details/remittance",
    "/cod/remittance",
    "/remittance",
    "/shipments",
    "/orders",
)


@router.get("/shiprocket-probe", response_model=ApiResponse[dict])
async def shiprocket_probe(
    user: User = Depends(require_permission("reconciliation.manage")),
) -> ApiResponse[dict]:
    """Superuser-only, read-only. GETs candidate Shiprocket finance
    endpoints through the existing client (existing credentials) and
    reports only HTTP outcome + response key names/types, so the real
    remittance endpoint and field names can be confirmed from production
    without exposing any value, token or customer data. Set
    `SHIPROCKET_REMITTANCE_PATH` to the path that returns remittance rows.
    """
    if not user.is_superuser:
        raise AuthorizationError("Superuser only.")
    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    if not isinstance(adapter, ShiprocketAdapter):
        raise IntegrationError("Shiprocket is not configured.")
    client = adapter._get_client()  # noqa: SLF001 - same shared client/token
    report: dict[str, object] = {}
    for path in PROBE_PATHS:
        try:
            data = await client.request("GET", path, params={"page": 1, "per_page": 1})
            report[path] = {"ok": True, "shape": _shape(data)}
        except Exception as exc:  # noqa: BLE001
            report[path] = {
                "ok": False,
                "error_type": getattr(exc, "error_type", type(exc).__name__),
                "status_code": getattr(exc, "status_code", None),
            }
    return ApiResponse(
        data={"configured_path": settings.SHIPROCKET_REMITTANCE_PATH, "probe": report}
    )


class SyncResult(BaseModel):
    created: int
    updated: int
    skipped: int


@router.post("/sync", response_model=ApiResponse[SyncResult])
async def sync_cod_remittances(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.manage")),
) -> ApiResponse[SyncResult]:
    """Pulls remittance data now (the same job also runs on a schedule)."""
    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    if not isinstance(adapter, ShiprocketAdapter):
        raise IntegrationError("Shiprocket is not configured.")
    result = await CodReconciliationService(session).sync_remittances(adapter)
    return ApiResponse(data=SyncResult(**result), message="COD remittance sync complete.")


class BankCreditVerification(BaseModel):
    amount: Decimal
    reference: str
    credited_on: datetime


@router.post("/remittances/{awb}/verify-bank-credit", response_model=ApiResponse[dict])
async def verify_bank_credit(
    awb: str,
    body: BankCreditVerification,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.manage")),
) -> ApiResponse[dict]:
    """Records that a person matched this remittance to a real bank credit.
    Shiprocket-reported remittance alone never counts as verified."""
    rem = await CodReconciliationService(session).verify_bank_credit(
        awb,
        amount=body.amount,
        reference=body.reference,
        credited_on=body.credited_on,
        user_id=user.id,
    )
    return ApiResponse(data={"awb": rem.awb, "bank_verified": True})
