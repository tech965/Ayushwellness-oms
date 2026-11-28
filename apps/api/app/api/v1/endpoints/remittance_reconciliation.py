"""Modules 6 & 7 -- COD / Prepaid Remittance Reconciliation endpoints.

Mounted at `/api/v1/remittance-reconciliation`:

  /cod/...        Module 6 (shipping-partner COD remittances)
  /prepaid/...    Module 7 (payment-gateway settlements)
  /bank-statements, /files/{id}/download   shared evidence

Permissions: `reconciliation.read` to view/export, `reconciliation.upload`
to upload reports/statements, `reconciliation.adjust` for manual matching,
bank linking and adjustments. Every write is audit-logged. Computed from
locally stored data, so unavailable in live-data mode like Modules 2-4.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ServiceUnavailableError, ValidationError
from app.core.logging import get_logger
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.dependencies.live_mode import unavailable_in_live_mode_for
from app.dependencies.pagination import pagination_params
from app.models.auth import User
from app.models.cod_remittance import CodRemittance
from app.schemas.common import PageParams, build_pagination_meta
from app.schemas.remittance_reconciliation import (
    AdjustmentRequest,
    BankCreditRow,
    BankLinkRequest,
    CashfreeSyncRequest,
    CodRecordRow,
    CrfTransactionRow,
    DrilldownResponse,
    ManualMatchRequest,
    PrepaidRecordRow,
    SettlementFileRow,
    SourcesResponse,
    SourceStatus,
    SummaryResponse,
    UnmatchedRow,
    UploadPreview,
    UploadResult,
)
from app.schemas.response import ApiResponse, PaginatedResponse
from app.services import cod_remittance_recon_service as cod_mod
from app.services import prepaid_remittance_recon_service as prepaid_mod
from app.services import remittance_upload_parser as parser
from app.services.cashfree_gateway_sync_service import (
    QUEUE_TTL,
    CashfreeGatewaySyncService,
)
from app.services.remittance_bank_service import BankStatementService
from app.services.remittance_upload_parser import MAX_FILE_BYTES
from app.tasks.cashfree_gateway_sync import sync_cashfree_manual_task

logger = get_logger(__name__)
_LIVE = (
    "Remittance reconciliation is computed from locally stored orders, remittances and bank data."
)
_GUARD = [Depends(unavailable_in_live_mode_for(_LIVE))]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
COD_STATUS = "pending|partially_reconciled|matched|short|excess"
PREPAID_STATUS = "pending|partially_reconciled|matched|short|excess|failed|refunded"

router = APIRouter()
cod = APIRouter(prefix="/cod", tags=["remittance-reconciliation:cod"], dependencies=_GUARD)
prepaid = APIRouter(
    prefix="/prepaid", tags=["remittance-reconciliation:prepaid"], dependencies=_GUARD
)
common = APIRouter(tags=["remittance-reconciliation"], dependencies=_GUARD)


async def _read_upload(file: UploadFile) -> bytes:
    return await file.read(MAX_FILE_BYTES + 1)


def _column_map(raw: str | None) -> dict[str, str] | None:
    """The explicit field -> header choices made in the upload dialog (JSON)."""
    if not raw or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValidationError("column_map must be valid JSON.") from exc
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ValidationError("column_map must map field names to header text.")
    return value


async def _preview(kind: str, file: UploadFile) -> UploadPreview:
    analysis, _table = parser.analyze_headers(
        kind, file.filename or "upload.csv", await _read_upload(file)
    )
    return UploadPreview(
        headers=[h for h in analysis.headers if h],
        suggested_mapping=analysis.mapped_headers,
        fields=[
            {"field": f, "label": spec[0], "template_header": spec[1]}
            for f, spec in parser.SPECS[kind].items()
        ],
        required_groups=parser.REQUIRED_GROUPS[kind],
        missing=analysis.missing,
        ambiguous=analysis.ambiguous,
        ignored=analysis.ignored,
        sample_rows=analysis.sample_rows,
        can_import=not analysis.problems,
        problems=analysis.problems,
    )


def _template(kind: str, name: str) -> Response:
    return Response(
        parser.template_csv(kind),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )


def _as_utc(value: datetime | None) -> datetime | None:
    """A date with no timezone (`2026-10-28`, `2026-10-28T00:00:00`) parses as a
    naive datetime; the services compare it in Python with timezone-aware stored
    timestamps, which raises TypeError (HTTP 500). Treat naive input as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _cod_filters(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    partner: str | None = Query(default=None, max_length=128),
    status: str | None = Query(default=None, pattern=f"^({COD_STATUS})$"),
    q: str | None = Query(default=None, max_length=100),
) -> cod_mod.Filters:
    return cod_mod.Filters(_as_utc(date_from), _as_utc(date_to), partner, status, q)


def _prepaid_filters(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    gateway: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, pattern=f"^({PREPAID_STATUS})$"),
    q: str | None = Query(default=None, max_length=100),
) -> prepaid_mod.Filters:
    return prepaid_mod.Filters(_as_utc(date_from), _as_utc(date_to), gateway, status, q)


def _drill_page(rows: list, total: int, totals: dict, page_params: PageParams, model):  # noqa: ANN001, ANN202
    return DrilldownResponse[model](
        data=[model(**r) for r in rows],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
        totals=totals,
    )


def _xlsx(data: bytes, name: str) -> Response:
    return Response(
        data, media_type=XLSX, headers={"Content-Disposition": f"attachment; filename={name}"}
    )


def _page(rows: list, total: int, page_params: PageParams, model):  # noqa: ANN001, ANN202
    return PaginatedResponse(
        data=[model(**r) for r in rows],
        meta=build_pagination_meta(total_items=total, page_params=page_params),
    )


# ===================================================================== COD


@cod.get("/summary", response_model=ApiResponse[SummaryResponse])
async def cod_summary(
    f: cod_mod.Filters = Depends(_cod_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[SummaryResponse]:
    data = await cod_mod.CodRemittanceReconciliationService(session).summary(f)
    return ApiResponse(data=SummaryResponse(**data))


@cod.get("/records", response_model=DrilldownResponse[CodRecordRow])
async def cod_records(
    age_bucket: str | None = Query(default=None, max_length=20),
    settled_on: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    kpi: str | None = Query(default=None, pattern="^(" + "|".join(cod_mod.SELECTORS) + ")$"),
    f: cod_mod.Filters = Depends(_cod_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> DrilldownResponse[CodRecordRow]:
    f.age_bucket, f.settled_on = age_bucket, settled_on
    rows, total, totals = await cod_mod.CodRemittanceReconciliationService(session).records(
        f, kpi=kpi, page_params=page_params
    )
    return _drill_page(rows, total, totals, page_params, CodRecordRow)


@cod.get("/unmatched", response_model=DrilldownResponse[UnmatchedRow])
async def cod_unmatched(
    kind: Literal["remittance_line", "bank_credit"] | None = Query(default=None),
    f: cod_mod.Filters = Depends(_cod_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> DrilldownResponse[UnmatchedRow]:
    rows, total, totals = await cod_mod.CodRemittanceReconciliationService(session).unmatched(
        f, kind=kind, page_params=page_params
    )
    return _drill_page(rows, total, totals, page_params, UnmatchedRow)


@cod.get("/crf-transactions", response_model=PaginatedResponse[CrfTransactionRow])
async def cod_crf_transactions(
    status: Literal["success", "initiated", "pending"] | None = Query(default=None),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> PaginatedResponse[CrfTransactionRow]:
    rows, total = await cod_mod.CodRemittanceReconciliationService(session).crf_transactions(
        status=status, page_params=page_params
    )
    return _page(rows, total, page_params, CrfTransactionRow)


@cod.get("/drilldown/export")
async def cod_drilldown_export(
    view: Literal["records", "unmatched"] = Query(default="records"),
    kpi: str | None = Query(default=None, pattern="^(" + "|".join(cod_mod.SELECTORS) + ")$"),
    kind: Literal["remittance_line", "bank_credit"] | None = Query(default=None),
    age_bucket: str | None = Query(default=None, max_length=20),
    settled_on: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    f: cod_mod.Filters = Depends(_cod_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> Response:
    """Excel of exactly the records in the open drilldown (all pages), with
    their total. Viewing permission is enough: nothing is changed."""
    f.age_bucket, f.settled_on = age_bucket, settled_on
    data = await cod_mod.CodRemittanceReconciliationService(session).export_drilldown_xlsx(
        f, kpi=kpi, unmatched=view == "unmatched", kind=kind
    )
    return _xlsx(data, "cod-reconciliation-drilldown.xlsx")


@cod.get("/orders/{order_id}/detail", response_model=ApiResponse[dict])
async def cod_order_detail(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[dict]:
    return ApiResponse(
        data=await cod_mod.CodRemittanceReconciliationService(session).order_detail(order_id)
    )


@cod.get("/reports/partners", response_model=ApiResponse[list[dict]])
async def cod_report_partners(
    f: cod_mod.Filters = Depends(_cod_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[list[dict]]:
    return ApiResponse(
        data=await cod_mod.CodRemittanceReconciliationService(session).report_partners(f)
    )


@cod.get("/reports/settlement-dates", response_model=ApiResponse[list[dict]])
async def cod_report_settlement_dates(
    f: cod_mod.Filters = Depends(_cod_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[list[dict]]:
    svc = cod_mod.CodRemittanceReconciliationService(session)
    return ApiResponse(data=await svc.report_settlement_dates(f))


@cod.get("/reports/ageing", response_model=ApiResponse[list[dict]])
async def cod_report_ageing(
    f: cod_mod.Filters = Depends(_cod_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[list[dict]]:
    return ApiResponse(
        data=await cod_mod.CodRemittanceReconciliationService(session).report_ageing(f)
    )


@cod.get("/bank-credits", response_model=PaginatedResponse[BankCreditRow])
async def cod_bank_credits(
    f: cod_mod.Filters = Depends(_cod_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> PaginatedResponse[BankCreditRow]:
    rows, total = await cod_mod.CodRemittanceReconciliationService(session).report_bank_credits(
        f, page_params
    )
    return _page(rows, total, page_params, BankCreditRow)


@cod.get("/export")
async def cod_export(
    f: cod_mod.Filters = Depends(_cod_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> Response:
    data = await cod_mod.CodRemittanceReconciliationService(session).export_xlsx(f)
    return Response(
        data,
        media_type=XLSX,
        headers={"Content-Disposition": "attachment; filename=cod-remittance-reconciliation.xlsx"},
    )


@cod.get("/uploads", response_model=ApiResponse[list[SettlementFileRow]])
async def cod_uploads(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[list[SettlementFileRow]]:
    files = await BankStatementService(session).list_uploads("cod")
    return ApiResponse(
        data=[SettlementFileRow.model_validate(f, from_attributes=True) for f in files]
    )


@cod.post("/uploads", response_model=ApiResponse[UploadResult], status_code=201)
async def cod_upload(
    partner: str = Form(..., max_length=128),
    column_map: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[UploadResult]:
    result = await cod_mod.CodRemittanceReconciliationService(session).import_report(
        partner=partner,
        filename=file.filename or "upload.csv",
        content_type=file.content_type,
        data=await _read_upload(file),
        user=user,
        column_map=_column_map(column_map),
    )
    return ApiResponse(data=UploadResult(**result), message="Remittance report imported.")


@cod.post("/uploads/preview", response_model=ApiResponse[UploadPreview])
async def cod_upload_preview(
    file: UploadFile = File(...),
    _: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[UploadPreview]:
    return ApiResponse(data=await _preview("cod", file))


@cod.get("/uploads/template")
async def cod_upload_template(
    _: User = Depends(require_permission("reconciliation.upload")),
) -> Response:
    return _template("cod", "cod-remittance-template.csv")


@cod.post("/rematch", response_model=ApiResponse[dict])
async def cod_rematch(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    return ApiResponse(data=await cod_mod.CodRemittanceReconciliationService(session).rematch(user))


@cod.post("/lines/{line_id}/match", response_model=ApiResponse[dict])
async def cod_manual_match(
    line_id: uuid.UUID,
    payload: ManualMatchRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    await cod_mod.CodRemittanceReconciliationService(session).manual_match(
        line_id,
        payload.order_id,
        user,
        confirm=payload.confirm,
        acknowledge_difference=payload.acknowledge_difference,
    )
    return ApiResponse(data={"matched": True})


@cod.get("/lines/{line_id}/lookup", response_model=ApiResponse[dict])
async def cod_lookup_order_for_line(
    line_id: uuid.UUID,
    q: str = Query(..., min_length=1, max_length=64),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    """Exact OMS order id / order number / AWB -> what matching this
    remittance line to that order would do (amounts, partial / excess,
    blockers). A read-only preview; nothing is saved.
    """
    svc = cod_mod.CodRemittanceReconciliationService(session)
    return ApiResponse(data=await svc.lookup_order_for_line(line_id, q))


@cod.post("/lines/{line_id}/unmatch", response_model=ApiResponse[dict])
async def cod_unmatch(
    line_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    await cod_mod.CodRemittanceReconciliationService(session).unmatch(line_id, user)
    return ApiResponse(data={"unmatched": True})


@cod.post("/bank-links", response_model=ApiResponse[dict], status_code=201)
async def cod_bank_link(
    payload: BankLinkRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    link = await BankStatementService(session).link_bank_credit(
        module="cod", user=user, **payload.model_dump()
    )
    return ApiResponse(data={"id": str(link.id)})


@cod.post("/bank-links/{link_id}/reverse", response_model=ApiResponse[dict])
async def cod_bank_unlink(
    link_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    await BankStatementService(session).reverse_bank_link("cod", link_id, user)
    return ApiResponse(data={"reversed": True})


@cod.post("/adjustments", response_model=ApiResponse[dict], status_code=201)
async def cod_adjustment(
    payload: AdjustmentRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    adj = await cod_mod.CodRemittanceReconciliationService(session).add_adjustment(
        user=user, **payload.model_dump()
    )
    return ApiResponse(data={"id": str(adj.id)})


@cod.post("/adjustments/{adjustment_id}/reverse", response_model=ApiResponse[dict])
async def cod_adjustment_reverse(
    adjustment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    await BankStatementService(session).reverse_adjustment("cod", adjustment_id, user)
    return ApiResponse(data={"reversed": True})


# ================================================================= PREPAID


@prepaid.get("/summary", response_model=ApiResponse[SummaryResponse])
async def prepaid_summary(
    f: prepaid_mod.Filters = Depends(_prepaid_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[SummaryResponse]:
    data = await prepaid_mod.PrepaidRemittanceReconciliationService(session).summary(f)
    return ApiResponse(data=SummaryResponse(**data))


@prepaid.get("/records", response_model=DrilldownResponse[PrepaidRecordRow])
async def prepaid_records(
    age_bucket: str | None = Query(default=None, max_length=20),
    settled_on: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    kpi: str | None = Query(default=None, pattern="^(" + "|".join(prepaid_mod.SELECTORS) + ")$"),
    f: prepaid_mod.Filters = Depends(_prepaid_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> DrilldownResponse[PrepaidRecordRow]:
    f.age_bucket, f.settled_on = age_bucket, settled_on
    rows, total, totals = await prepaid_mod.PrepaidRemittanceReconciliationService(session).records(
        f, kpi=kpi, page_params=page_params
    )
    return _drill_page(rows, total, totals, page_params, PrepaidRecordRow)


@prepaid.get("/unmatched", response_model=DrilldownResponse[UnmatchedRow])
async def prepaid_unmatched(
    kind: Literal["gateway_line", "bank_credit"] | None = Query(default=None),
    f: prepaid_mod.Filters = Depends(_prepaid_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> DrilldownResponse[UnmatchedRow]:
    rows, total, totals = await prepaid_mod.PrepaidRemittanceReconciliationService(
        session
    ).unmatched(f, kind=kind, page_params=page_params)
    return _drill_page(rows, total, totals, page_params, UnmatchedRow)


@prepaid.get("/drilldown/export")
async def prepaid_drilldown_export(
    view: Literal["records", "unmatched"] = Query(default="records"),
    kpi: str | None = Query(default=None, pattern="^(" + "|".join(prepaid_mod.SELECTORS) + ")$"),
    kind: Literal["gateway_line", "bank_credit"] | None = Query(default=None),
    age_bucket: str | None = Query(default=None, max_length=20),
    settled_on: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    f: prepaid_mod.Filters = Depends(_prepaid_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> Response:
    """Excel of exactly the records in the open drilldown (all pages), with
    their total. Viewing permission is enough: nothing is changed."""
    f.age_bucket, f.settled_on = age_bucket, settled_on
    data = await prepaid_mod.PrepaidRemittanceReconciliationService(session).export_drilldown_xlsx(
        f, kpi=kpi, unmatched=view == "unmatched", kind=kind
    )
    return _xlsx(data, "prepaid-reconciliation-drilldown.xlsx")


@prepaid.get("/orders/{order_id}/detail", response_model=ApiResponse[dict])
async def prepaid_order_detail(
    order_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[dict]:
    return ApiResponse(
        data=await prepaid_mod.PrepaidRemittanceReconciliationService(session).order_detail(
            order_id
        )
    )


@prepaid.get("/reports/gateways", response_model=ApiResponse[list[dict]])
async def prepaid_report_gateways(
    f: prepaid_mod.Filters = Depends(_prepaid_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[list[dict]]:
    svc = prepaid_mod.PrepaidRemittanceReconciliationService(session)
    return ApiResponse(data=await svc.report_gateways(f))


@prepaid.get("/reports/settlement-dates", response_model=ApiResponse[list[dict]])
async def prepaid_report_settlement_dates(
    f: prepaid_mod.Filters = Depends(_prepaid_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[list[dict]]:
    svc = prepaid_mod.PrepaidRemittanceReconciliationService(session)
    return ApiResponse(data=await svc.report_settlement_dates(f))


@prepaid.get("/reports/ageing", response_model=ApiResponse[list[dict]])
async def prepaid_report_ageing(
    f: prepaid_mod.Filters = Depends(_prepaid_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[list[dict]]:
    svc = prepaid_mod.PrepaidRemittanceReconciliationService(session)
    return ApiResponse(data=await svc.report_ageing(f))


@prepaid.get("/settlement-lots", response_model=PaginatedResponse[dict])
async def prepaid_settlement_lots(
    f: prepaid_mod.Filters = Depends(_prepaid_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> PaginatedResponse[dict]:
    rows, total = await prepaid_mod.PrepaidRemittanceReconciliationService(
        session
    ).report_settlement_lots(f, page_params)
    return PaginatedResponse(
        data=rows, meta=build_pagination_meta(total_items=total, page_params=page_params)
    )


@prepaid.get("/bank-credits", response_model=PaginatedResponse[BankCreditRow])
async def prepaid_bank_credits(
    f: prepaid_mod.Filters = Depends(_prepaid_filters),
    page_params: PageParams = Depends(pagination_params),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> PaginatedResponse[BankCreditRow]:
    rows, total = await cod_mod.CodRemittanceReconciliationService(session).report_bank_credits(
        f, page_params, module="prepaid"
    )
    return _page(rows, total, page_params, BankCreditRow)


@prepaid.get("/export")
async def prepaid_export(
    f: prepaid_mod.Filters = Depends(_prepaid_filters),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> Response:
    data = await prepaid_mod.PrepaidRemittanceReconciliationService(session).export_xlsx(f)
    return Response(
        data,
        media_type=XLSX,
        headers={
            "Content-Disposition": "attachment; filename=prepaid-remittance-reconciliation.xlsx"
        },
    )


@prepaid.get("/uploads", response_model=ApiResponse[list[SettlementFileRow]])
async def prepaid_uploads(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[list[SettlementFileRow]]:
    files = await BankStatementService(session).list_uploads("prepaid")
    return ApiResponse(
        data=[SettlementFileRow.model_validate(f, from_attributes=True) for f in files]
    )


@prepaid.get("/cashfree/status", response_model=ApiResponse[dict])
async def cashfree_sync_status(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[dict]:
    """Connection status and last (successful) sync of the Cashfree API
    source. Read-only; makes no Cashfree call and never returns credentials.
    """
    return ApiResponse(data=await CashfreeGatewaySyncService(session).status())


ENQUEUE_TIMEOUT_SECONDS = 10


async def _publish_manual_sync(
    user_id: uuid.UUID, date_from: datetime | None, date_to: datetime | None
) -> None:
    """Put the task on the broker. No publish retries and a hard time limit, so
    an unreachable broker cannot hold the request open."""
    await asyncio.wait_for(
        asyncio.to_thread(
            sync_cashfree_manual_task.apply_async,
            args=[
                str(user_id),
                date_from.isoformat() if date_from else None,
                date_to.isoformat() if date_to else None,
            ],
            retry=False,
            expires=int(QUEUE_TTL.total_seconds()),
        ),
        timeout=ENQUEUE_TIMEOUT_SECONDS,
    )


@prepaid.post("/cashfree/sync", response_model=ApiResponse[dict])
async def cashfree_sync_now(
    body: CashfreeSyncRequest | None = None,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[dict]:
    """Sync Now: queue a Celery task that pulls Cashfree transactions,
    settlements and transaction-level settlement rows, and return at once. Same
    permission as uploading a report. Poll `/cashfree/status` for progress.
    Returns "not_configured" without any fake data when credentials are
    missing.
    """
    body = body or CashfreeSyncRequest()
    svc = CashfreeGatewaySyncService(session)
    status = await svc.status()
    if not status["configured"]:
        return ApiResponse(
            data={"status": "not_configured", "message": status["connection_message"]}
        )
    user_id = user.id  # the session commits below would expire the object
    outcome, marker = await svc.claim_queue_slot()
    if marker is None:
        message = (
            "A Cashfree sync is already running."
            if outcome == "already_running"
            else "A Cashfree sync is already queued."
        )
        return ApiResponse(data={"status": outcome, "message": message})
    try:
        await _publish_manual_sync(user_id, body.date_from, body.date_to)
    except Exception as exc:  # noqa: BLE001 - any broker failure means "not queued"
        logger.error("cashfree_manual_sync_enqueue_failed", error=type(exc).__name__)
        await svc.abandon_queue_slot(
            marker, f"Could not queue the sync ({type(exc).__name__}). Check the task broker."
        )
        raise ServiceUnavailableError(
            "The sync could not be queued because the task broker is unavailable. "
            "Nothing was synced; try again once it is back."
        ) from exc
    return ApiResponse(data={"status": "queued", "message": "Cashfree sync queued."})


@prepaid.post("/uploads", response_model=ApiResponse[UploadResult], status_code=201)
async def prepaid_upload(
    gateway: str = Form(..., max_length=64),
    column_map: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[UploadResult]:
    result = await prepaid_mod.PrepaidRemittanceReconciliationService(session).import_report(
        gateway=gateway,
        filename=file.filename or "upload.csv",
        content_type=file.content_type,
        data=await _read_upload(file),
        user=user,
        column_map=_column_map(column_map),
    )
    return ApiResponse(data=UploadResult(**result), message="Settlement report imported.")


@prepaid.post("/uploads/preview", response_model=ApiResponse[UploadPreview])
async def prepaid_upload_preview(
    file: UploadFile = File(...),
    _: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[UploadPreview]:
    return ApiResponse(data=await _preview("prepaid", file))


@prepaid.get("/uploads/template")
async def prepaid_upload_template(
    _: User = Depends(require_permission("reconciliation.upload")),
) -> Response:
    return _template("prepaid", "prepaid-settlement-template.csv")


@prepaid.post("/rematch", response_model=ApiResponse[dict])
async def prepaid_rematch(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    svc = prepaid_mod.PrepaidRemittanceReconciliationService(session)
    return ApiResponse(data=await svc.rematch(user))


@prepaid.post("/lines/{line_id}/match", response_model=ApiResponse[dict])
async def prepaid_manual_match(
    line_id: uuid.UUID,
    payload: ManualMatchRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    svc = prepaid_mod.PrepaidRemittanceReconciliationService(session)
    await svc.manual_match(line_id, payload.order_id, user)
    return ApiResponse(data={"matched": True})


@prepaid.post("/lines/{line_id}/unmatch", response_model=ApiResponse[dict])
async def prepaid_unmatch(
    line_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    await prepaid_mod.PrepaidRemittanceReconciliationService(session).unmatch(line_id, user)
    return ApiResponse(data={"unmatched": True})


@prepaid.post("/bank-links", response_model=ApiResponse[dict], status_code=201)
async def prepaid_bank_link(
    payload: BankLinkRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    link = await BankStatementService(session).link_bank_credit(
        module="prepaid", user=user, **payload.model_dump()
    )
    return ApiResponse(data={"id": str(link.id)})


@prepaid.post("/bank-links/{link_id}/reverse", response_model=ApiResponse[dict])
async def prepaid_bank_unlink(
    link_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    await BankStatementService(session).reverse_bank_link("prepaid", link_id, user)
    return ApiResponse(data={"reversed": True})


@prepaid.post("/adjustments", response_model=ApiResponse[dict], status_code=201)
async def prepaid_adjustment(
    payload: AdjustmentRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    adj = await prepaid_mod.PrepaidRemittanceReconciliationService(session).add_adjustment(
        user=user, **payload.model_dump()
    )
    return ApiResponse(data={"id": str(adj.id)})


@prepaid.post("/adjustments/{adjustment_id}/reverse", response_model=ApiResponse[dict])
async def prepaid_adjustment_reverse(
    adjustment_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.adjust")),
) -> ApiResponse[dict]:
    await BankStatementService(session).reverse_adjustment("prepaid", adjustment_id, user)
    return ApiResponse(data={"reversed": True})


# ================================================================= COMMON


@common.post("/bank-statements", response_model=ApiResponse[UploadResult], status_code=201)
async def upload_bank_statement(
    source_name: str = Form(default="Bank statement", max_length=128),
    column_map: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[UploadResult]:
    result = await BankStatementService(session).import_statement(
        source_name=source_name,
        filename=file.filename or "statement.csv",
        content_type=file.content_type,
        data=await _read_upload(file),
        user=user,
        column_map=_column_map(column_map),
    )
    return ApiResponse(data=UploadResult(**result), message="Bank statement imported.")


@common.post("/bank-statements/preview", response_model=ApiResponse[UploadPreview])
async def bank_statement_preview(
    file: UploadFile = File(...),
    _: User = Depends(require_permission("reconciliation.upload")),
) -> ApiResponse[UploadPreview]:
    return ApiResponse(data=await _preview("bank", file))


@common.get("/bank-statements/template")
async def bank_statement_template(
    _: User = Depends(require_permission("reconciliation.upload")),
) -> Response:
    return _template("bank", "bank-statement-template.csv")


@common.get("/files/{file_id}/download")
async def download_original_file(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> Response:
    record = await BankStatementService(session).get_file(file_id)
    return Response(
        record.content,
        media_type=record.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{record.filename}"'},
    )


@common.get("/sources", response_model=ApiResponse[SourcesResponse])
async def integration_sources(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("reconciliation.read")),
) -> ApiResponse[SourcesResponse]:
    """Which settlement sources can be fetched by API and which need an
    uploaded report. Reports configuration *status* only -- never values.
    """
    sr_last = await session.scalar(select(func.max(CodRemittance.synced_at)))
    sr_ok = bool(settings.SHIPROCKET_EMAIL and settings.SHIPROCKET_PASSWORD)
    cf = await CashfreeGatewaySyncService(session).status()
    cf_ok = cf["configured"] and cf["connection_status"] != "not_configured"
    data = SourcesResponse(
        cod=[
            SourceStatus(
                key="shiprocket",
                label="Shiprocket",
                api_supported=True,
                api_configured=sr_ok,
                last_synced_at=sr_last,
                note=(
                    "COD remittance fetched through the existing Shiprocket integration."
                    if sr_ok
                    else "Shiprocket credentials are not configured; upload the remittance report."
                ),
            ),
            SourceStatus(
                key="other",
                label="Other shipping partners",
                api_supported=False,
                api_configured=False,
                note="No API integration exists for other partners; upload their report.",
            ),
        ],
        prepaid=[
            SourceStatus(
                key="cashfree",
                label="Cashfree",
                api_supported=True,
                api_configured=cf_ok,
                last_synced_at=cf["last_successful_sync_at"],
                connection_status=cf["connection_status"],
                note=(
                    "Transactions, settlements and settlement rows are synchronised "
                    "automatically from the Cashfree API."
                    if cf_ok
                    else "Not Configured: " + cf["connection_message"]
                ),
            ),
            SourceStatus(
                key="other",
                label="Other payment gateways",
                api_supported=False,
                api_configured=False,
                note="No other gateway integration exists; upload the gateway settlement report.",
            ),
        ],
    )
    return ApiResponse(data=data)


router.include_router(cod)
router.include_router(prepaid)
router.include_router(common)
