"""Shiprocket wallet: balance, ledger, summaries, recharge-claim tracking.

Read-only toward Shiprocket (balance, serviceability, optional statement).
There is deliberately NO endpoint that recharges the wallet or initiates a
payment -- recharging is done in the Shiprocket dashboard.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

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
from app.services.shiprocket_wallet_service import (
    TxnRow,
    WalletService,
    WalletState,
    WalletSummary,
)

router = APIRouter()

RECHARGE_INSTRUCTIONS = (
    "Recharge the wallet in the Shiprocket dashboard (Billing → Wallet → Add Money). "
    "The OMS never initiates payments. Afterwards click 'Refresh balance'; if you record "
    "a recharge here, it only counts once Shiprocket's own balance shows the increase."
)


def _adapter() -> ShiprocketAdapter:
    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    if not isinstance(adapter, ShiprocketAdapter):
        raise IntegrationError("Shiprocket is not configured.")
    return adapter


def _filters(
    q: str | None = Query(default=None, description="Order number, AWB or transaction reference"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    txn_type: str | None = Query(default=None),
    direction: Literal["debit", "credit"] | None = Query(default=None),
    courier: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    return {
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "txn_type": txn_type,
        "direction": direction,
        "courier": courier,
        "status": status,
    }


@router.get("/state", response_model=ApiResponse[WalletState])
async def wallet_state(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[WalletState]:
    return ApiResponse(data=await WalletService(session).state())


@router.post("/refresh", response_model=ApiResponse[WalletState])
async def refresh_balance(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[WalletState]:
    """Fetches the live balance now and stores a snapshot (read-only on Shiprocket)."""
    svc = WalletService(session)
    await svc.refresh_balance(_adapter(), trigger="manual")
    return ApiResponse(data=await svc.state(), message="Wallet balance refreshed.")


@router.get("/summary", response_model=ApiResponse[WalletSummary])
async def wallet_summary(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[WalletSummary]:
    return ApiResponse(
        data=await WalletService(session).summary(date_from=date_from, date_to=date_to)
    )


@router.get("/transactions", response_model=PaginatedResponse[TxnRow])
async def list_transactions(
    filters: dict = Depends(_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> PaginatedResponse[TxnRow]:
    rows, total = await WalletService(session).list_transactions(
        page=page_params.page, page_size=page_params.page_size, **filters
    )
    return PaginatedResponse(
        data=rows, meta=build_pagination_meta(total_items=total, page_params=page_params)
    )


@router.get("/transactions/export")
async def export_transactions(
    filters: dict = Depends(_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> StreamingResponse:
    workbook = await WalletService(session).export_xlsx(**filters)
    return StreamingResponse(
        iter([workbook]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=shiprocket-wallet-transactions.xlsx"},
    )


@router.get("/periods", response_model=ApiResponse[list[dict]])
async def period_summary(
    by: Literal["month", "year"] = Query(default="month"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[list[dict]]:
    return ApiResponse(
        data=await WalletService(session).monthly_summary(
            date_from=date_from, date_to=date_to, by=by
        )
    )


@router.get("/order-expenses", response_model=ApiResponse[dict])
async def order_expenses(
    order_number: str | None = Query(default=None),
    awb: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[dict]:
    return ApiResponse(
        data=await WalletService(session).order_expenses(order_number=order_number, awb=awb)
    )


class SyncResult(BaseModel):
    created: int
    updated: int
    skipped: int
    unparsed: int


@router.post("/sync", response_model=ApiResponse[SyncResult])
async def sync_ledger(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.manage")),
) -> ApiResponse[SyncResult]:
    """Pulls the wallet statement. Disabled until SHIPROCKET_WALLET_STATEMENT_PATH is set."""
    result = await WalletService(session).sync_transactions(_adapter())
    return ApiResponse(data=SyncResult(**result), message="Wallet transaction sync complete.")


class RechargeClaim(BaseModel):
    amount: Decimal
    payment_reference: str
    note: str | None = None


@router.get("/recharge-instructions", response_model=ApiResponse[dict])
async def recharge_instructions(
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[dict]:
    return ApiResponse(data={"instructions": RECHARGE_INSTRUCTIONS})


@router.post("/recharge-claims", response_model=ApiResponse[TxnRow], status_code=201)
async def create_recharge_claim(
    body: RechargeClaim,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.manage")),
) -> ApiResponse[TxnRow]:
    """Logs that a recharge was made. UNVERIFIED and excluded from totals."""
    svc = WalletService(session)
    row = await svc.create_recharge_claim(
        amount=body.amount,
        payment_reference=body.payment_reference,
        note=body.note,
        user_id=user.id,
    )
    return ApiResponse(data=svc._row(row), message="Recorded as unverified; not counted yet.")  # noqa: SLF001


@router.post("/recharge-claims/{claim_id}/verify", response_model=ApiResponse[TxnRow])
async def verify_recharge_claim(
    claim_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.manage")),
) -> ApiResponse[TxnRow]:
    """Confirms a claim only if Shiprocket's live balance shows the increase."""
    svc = WalletService(session)
    row = await svc.confirm_claim_by_balance(_adapter(), claim_id, user_id=user.id)
    return ApiResponse(data=svc._row(row), message="Confirmed by wallet balance increase.")  # noqa: SLF001


def _shape(value: object, depth: int = 0) -> object:
    if isinstance(value, dict):
        return (
            "object"
            if depth >= 3
            else {k: _shape(v, depth + 1) for k, v in list(value.items())[:60]}
        )
    if isinstance(value, list):
        return [f"list[{len(value)}]", _shape(value[0], depth + 1)] if value else "list[0]"
    return type(value).__name__ if value is not None else "null"


@router.get("/probe", response_model=ApiResponse[dict])
async def probe(
    user: User = Depends(require_permission("reconciliation.manage")),
) -> ApiResponse[dict]:
    """Superuser-only. Calls the wallet balance endpoint (and the candidate
    statement paths) read-only and reports HTTP outcome + key names/types
    only -- never values -- so the real response shape can be confirmed in
    production without exposing balances, credentials or customer data."""
    if not user.is_superuser:
        raise AuthorizationError("Superuser only.")
    client = _adapter()._get_client()  # noqa: SLF001
    paths = (
        settings.SHIPROCKET_WALLET_BALANCE_PATH,
        "/account/details/statement",
        "/account/details/wallet-balance",
    )
    report: dict[str, object] = {}
    for path in dict.fromkeys(p for p in paths if p):
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
        data={
            "statement_path_configured": bool(settings.SHIPROCKET_WALLET_STATEMENT_PATH),
            "probe": report,
        }
    )
