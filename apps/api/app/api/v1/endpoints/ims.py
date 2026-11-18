"""Inventory Management System (IMS) API.

Permissions: `ims.read` (view), `ims.stock.write` (stock inward, transfers,
returns, adjustments) and `ims.manage` (warehouses, packaging setup,
conversions, external parties, opening balances, catalogue refresh).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IntegrationError, NotFoundError
from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.models.ims import PACKAGING_UNITS, ImsItem
from app.schemas import ims as s
from app.schemas.response import ApiResponse
from app.services import ims_catalog, ims_reports
from app.services.ims_service import IMSService

router = APIRouter()

READ = "ims.read"
WRITE = "ims.stock.write"
MANAGE = "ims.manage"


def _svc(session: AsyncSession, user: User) -> IMSService:
    return IMSService(session, user)


def _paged(res: dict[str, Any]) -> ApiResponse[dict]:
    return ApiResponse(data=res)


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard", response_model=ApiResponse[dict])
async def dashboard(
    warehouse_id: uuid.UUID | None = None,
    category: str | None = None,
    q: str | None = Query(default=None, max_length=120),
    date_from: date | None = None,
    date_to: date | None = None,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    data = await _svc(session, user).dashboard(
        warehouse_id=warehouse_id, category=category, q=q, date_from=date_from, date_to=date_to
    )
    # Catalogue figures come live from Shopify; if Shopify is unreachable the
    # stock figures still load and the catalogue block says why it is absent.
    try:
        snap = await ims_catalog.get_catalog()
        data["catalog"] = {
            **ims_catalog.catalog_stats(snap.products),
            "truncated": snap.truncated,
            "fetched_at": snap.fetched_at,
        }
        data["warnings"] = []
    except IntegrationError as exc:
        data["catalog"] = None
        data["warnings"] = [f"Shopify catalogue unavailable: {exc.message}"]
    data["categories"] = sorted(
        {*(await _svc(session, user).categories()), *({p["category"] for p in snap.products} if data["catalog"] else set())}
    )
    return ApiResponse(data=data)


# ---------------------------------------------------------------- catalogue
async def _register_lookup(session: AsyncSession, variant_ids: list[str]) -> dict[str, dict]:
    if not variant_ids:
        return {}
    items = (
        (await session.execute(select(ImsItem).where(ImsItem.shopify_variant_id.in_(variant_ids))))
        .scalars()
        .all()
    )
    qty = await IMSService(session)._qty_by_item(warehouse_id=None, item_ids=[i.id for i in items])  # noqa: SLF001
    return {i.shopify_variant_id: {"item_id": str(i.id), "total_stock": qty.get(i.id, 0)} for i in items}


@router.get("/catalog/products", response_model=ApiResponse[dict])
async def catalog_products(
    q: str | None = Query(default=None, max_length=120),
    category: str | None = None,
    status: str | None = Query(default=None, pattern="^(active|draft|archived)$"),
    missing_sku: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    snap = await ims_catalog.get_catalog()
    rows = ims_catalog.filter_products(
        snap.products, q=q, category=category, status=status, missing_sku=missing_sku
    )
    total = len(rows)
    page_rows = rows[(page - 1) * page_size : page * page_size]
    reg = await _register_lookup(
        session, [v["variant_id"] for p in page_rows for v in p["variants"]]
    )
    items = [
        {**p, "variants": [{**v, **reg.get(v["variant_id"], {"item_id": None, "total_stock": 0})}
                            for v in p["variants"]]}
        for p in page_rows
    ]
    return ApiResponse(
        data={
            "items": items, "total_items": total, "page": page, "page_size": page_size,
            "stats": ims_catalog.catalog_stats(snap.products),
            "fetched_at": snap.fetched_at, "truncated": snap.truncated,
        }
    )


@router.get("/catalog/products/{product_id}", response_model=ApiResponse[dict])
async def catalog_product(
    product_id: str,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    snap = await ims_catalog.get_catalog()
    product = next((p for p in snap.products if p["product_id"] == product_id), None)
    if product is None:
        raise NotFoundError("Product not found in the Shopify catalogue.")
    svc = _svc(session, user)
    variants = []
    for v in product["variants"]:
        item = (
            await session.execute(select(ImsItem).where(ImsItem.shopify_variant_id == v["variant_id"]))
        ).scalar_one_or_none()
        detail = await svc.item_detail(item.id) if item else None
        variants.append(
            {**v, "item_id": str(item.id) if item else None,
             "by_warehouse": detail["by_warehouse"] if detail else [],
             "total_stock": detail["total"] if detail else 0,
             "status": detail["status"] if detail else "not_tracked"}
        )
    return ApiResponse(data={**product, "variants": variants})


@router.get("/catalog/variants", response_model=ApiResponse[dict])
async def catalog_variants(
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    """Flat variant search used by stock forms' product pickers."""
    snap = await ims_catalog.get_catalog()
    products = ims_catalog.filter_products(snap.products, q=q, status="active")
    flat = [
        {
            "product_id": p["product_id"], "variant_id": v["variant_id"],
            "label": ims_catalog.variant_label(p, v), "sku": v["sku"], "sku_missing": v["sku_missing"],
            "category": p["category"], "image_url": v["image_url"], "barcode": v["barcode"],
        }
        for p in products for v in p["variants"]
    ][:limit]
    reg = await _register_lookup(session, [f["variant_id"] for f in flat])
    return ApiResponse(
        data={"items": [{**f, **reg.get(f["variant_id"], {"item_id": None, "total_stock": 0})} for f in flat]}
    )


@router.get("/categories", response_model=ApiResponse[dict])
async def categories_overview(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    """Herbal Masala / Ayurvedic Products / Nutraceutical Products with each
    category's live product list and actual per-warehouse stock, for the
    Categories page's inline accordion."""
    return ApiResponse(data=await _svc(session, user).categories_overview())


@router.get("/catalog/categories", response_model=ApiResponse[dict])
async def catalog_categories(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    snap = await ims_catalog.get_catalog()
    out: dict[str, dict[str, int]] = {}
    for p in snap.products:
        c = out.setdefault(p["category"], {"products": 0, "active_products": 0, "skus": 0})
        c["products"] += 1
        c["active_products"] += p["status"] == "active"
        c["skus"] += len(p["variants"])
    return ApiResponse(
        data={"items": [{"category": k, **v} for k, v in sorted(out.items())],
              "fetched_at": snap.fetched_at, "truncated": snap.truncated}
    )


@router.get("/filters/categories", response_model=ApiResponse[dict])
async def filter_categories(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    """Category choices for filters: categories of tracked items, plus
    Shopify's when reachable. Never fails because Shopify is down."""
    cats = set(await _svc(session, user).categories())
    shopify_ok = True
    try:
        cats |= {p["category"] for p in (await ims_catalog.get_catalog()).products}
    except IntegrationError:
        shopify_ok = False
    return ApiResponse(data={"items": sorted(cats), "shopify_available": shopify_ok})


@router.get("/filters/categories", response_model=ApiResponse[dict])
async def filter_categories(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    """Category choices for filters: categories of tracked items, plus
    Shopify's when reachable. Never fails because Shopify is down."""
    cats = set(await _svc(session, user).categories())
    shopify_ok = True
    try:
        cats |= {p["category"] for p in (await ims_catalog.get_catalog()).products}
    except IntegrationError:
        shopify_ok = False
    return ApiResponse(data={"items": sorted(cats), "shopify_available": shopify_ok})


@router.get("/filters/categories", response_model=ApiResponse[dict])
async def filter_categories(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    """Category choices for filters: categories of tracked items, plus
    Shopify's when reachable. Never fails because Shopify is down."""
    cats = set(await _svc(session, user).categories())
    shopify_ok = True
    try:
        cats |= {p["category"] for p in (await ims_catalog.get_catalog()).products}
    except IntegrationError:
        shopify_ok = False
    return ApiResponse(data={"items": sorted(cats), "shopify_available": shopify_ok})


@router.post("/catalog/refresh", response_model=ApiResponse[dict])
async def catalog_refresh(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
) -> ApiResponse[dict]:
    """Manual Shopify re-fetch. Only refreshes item labels; stock is untouched."""
    return ApiResponse(data=await ims_catalog.sync_item_labels(session))


# -------------------------------------------------------------------- items
@router.post("/items/finished", response_model=ApiResponse[dict])
async def resolve_finished(
    payload: s.FinishedItemResolve,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(WRITE)),
) -> ApiResponse[dict]:
    return ApiResponse(
        data=await _svc(session, user).resolve_finished_item(
            product_id=payload.product_id, variant_id=payload.variant_id
        )
    )


@router.get("/items", response_model=ApiResponse[dict])
async def search_items(
    q: str | None = Query(default=None, max_length=120),
    kind: str | None = Query(default=None, pattern="^(finished|packaging)$"),
    warehouse_id: uuid.UUID | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    items = await _svc(session, user).search_items(
        q=q, kind=kind, warehouse_id=warehouse_id, limit=limit
    )
    return ApiResponse(data={"items": items})


@router.get("/items/{item_id}", response_model=ApiResponse[dict])
async def item_detail(
    item_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).item_detail(item_id))


@router.patch("/items/{item_id}", response_model=ApiResponse[dict])
async def update_item(
    item_id: uuid.UUID,
    payload: s.ItemUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
) -> ApiResponse[dict]:
    return ApiResponse(
        data=await _svc(session, user).update_item(item_id, payload.model_dump(exclude_unset=True))
    )


# --------------------------------------------------------------- warehouses
@router.get("/warehouses", response_model=ApiResponse[dict])
async def list_warehouses(
    active_only: bool = False,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return ApiResponse(data={"items": await _svc(session, user).list_warehouses(active_only=active_only)})


@router.post("/warehouses", response_model=ApiResponse[dict], status_code=201)
async def create_warehouse(
    payload: s.WarehouseCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).create_warehouse(payload.model_dump()))


@router.patch("/warehouses/{warehouse_id}", response_model=ApiResponse[dict])
async def update_warehouse(
    warehouse_id: uuid.UUID,
    payload: s.WarehouseUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
) -> ApiResponse[dict]:
    return ApiResponse(
        data=await _svc(session, user).update_warehouse(
            warehouse_id, payload.model_dump(exclude_unset=True)
        )
    )


# ---------------------------------------------------------------- packaging
@router.get("/packaging", response_model=ApiResponse[dict])
async def list_packaging(
    warehouse_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=120),
    include_inactive: bool = False,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    rows = await _svc(session, user).list_packaging_products(
        warehouse_id=warehouse_id, q=q, include_inactive=include_inactive
    )
    return ApiResponse(data={"items": rows, "units": list(PACKAGING_UNITS)})


@router.post("/packaging", response_model=ApiResponse[dict], status_code=201)
async def create_packaging(
    payload: s.PackagingProductCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).create_packaging_product(payload.model_dump()))


@router.get("/packaging/{product_id}", response_model=ApiResponse[dict])
async def get_packaging(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).get_packaging_product(product_id))


@router.patch("/packaging/{product_id}", response_model=ApiResponse[dict])
async def update_packaging(
    product_id: uuid.UUID,
    payload: s.PackagingProductUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
) -> ApiResponse[dict]:
    return ApiResponse(
        data=await _svc(session, user).update_packaging_product(
            product_id, payload.model_dump(exclude_unset=True)
        )
    )


@router.put("/packaging/{product_id}/conversions", response_model=ApiResponse[dict])
async def set_conversions(
    product_id: uuid.UUID,
    payload: s.ConversionSet,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
) -> ApiResponse[dict]:
    return ApiResponse(
        data=await _svc(session, user).set_conversions(
            product_id, [r.model_dump() for r in payload.rules]
        )
    )


@router.post("/packaging/convert", response_model=ApiResponse[dict])
async def convert_units(
    payload: s.ConvertRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return ApiResponse(
        data=await _svc(session, user).convert(
            payload.packaging_product_id, payload.from_unit, payload.to_unit, payload.quantity
        )
    )


# -------------------------------------------------------------------- stock
@router.get("/stock", response_model=ApiResponse[dict])
async def stock_overview(
    warehouse_id: uuid.UUID | None = None,
    kind: str | None = Query(default=None, pattern="^(finished|packaging)$"),
    category: str | None = None,
    q: str | None = Query(default=None, max_length=120),
    status: str | None = Query(default=None, pattern="^(in_stock|low_stock|out_of_stock)$"),
    include_inactive: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return _paged(
        await _svc(session, user).stock_overview(
            warehouse_id=warehouse_id, kind=kind, category=category, q=q, status=status,
            page=page, page_size=page_size, include_inactive=include_inactive,
        )
    )


@router.get("/stock/as-of", response_model=ApiResponse[dict])
async def stock_as_of(
    as_of: date,
    warehouse_id: uuid.UUID | None = None,
    kind: str | None = Query(default=None, pattern="^(finished|packaging)$"),
    category: str | None = None,
    q: str | None = Query(default=None, max_length=120),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    rows = await _svc(session, user).stock_as_of(
        as_of, warehouse_id=warehouse_id, kind=kind, category=category, q=q
    )
    return ApiResponse(data={"as_of": str(as_of), "items": rows})


@router.post("/stock/inward", response_model=ApiResponse[dict], status_code=201)
async def stock_inward(
    payload: s.StockInRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(WRITE)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).stock_in(payload.model_dump()))


@router.post("/stock/adjustments", response_model=ApiResponse[dict], status_code=201)
async def create_adjustment(
    payload: s.AdjustmentRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(WRITE)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).adjust(payload.model_dump()))


@router.get("/stock/adjustments", response_model=ApiResponse[dict])
async def list_adjustments(
    warehouse_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    adjustment_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return _paged(
        await _svc(session, user).list_adjustments(
            warehouse_id=warehouse_id, item_id=item_id, adjustment_type=adjustment_type,
            date_from=date_from, date_to=date_to, page=page, page_size=page_size,
        )
    )


@router.post("/stock/opening-balances", response_model=ApiResponse[dict], status_code=201)
async def opening_balances(
    payload: s.OpeningBalanceRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).opening_balances(payload.model_dump()))


# ---------------------------------------------------------------- transfers
@router.post("/transfers", response_model=ApiResponse[dict], status_code=201)
async def create_transfer(
    payload: s.TransferRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(WRITE)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).transfer(payload.model_dump()))


@router.get("/transfers", response_model=ApiResponse[dict])
async def list_transfers(
    warehouse_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=120),
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return _paged(
        await _svc(session, user).list_transfers(
            warehouse_id=warehouse_id, item_id=item_id, q=q, date_from=date_from,
            date_to=date_to, page=page, page_size=page_size,
        )
    )


@router.get("/transfers/{transfer_id}", response_model=ApiResponse[dict])
async def get_transfer(
    transfer_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).get_transfer(transfer_id))


# ------------------------------------------------------------------ returns
@router.get("/parties", response_model=ApiResponse[dict])
async def list_parties(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return ApiResponse(data={"items": await _svc(session, user).list_parties()})


@router.post("/parties", response_model=ApiResponse[dict], status_code=201)
async def create_party(
    payload: s.PartyCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(WRITE)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).create_party(payload.model_dump()))


@router.patch("/parties/{party_id}", response_model=ApiResponse[dict])
async def update_party(
    party_id: uuid.UUID,
    payload: s.PartyUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(MANAGE)),
) -> ApiResponse[dict]:
    return ApiResponse(
        data=await _svc(session, user).update_party(party_id, payload.model_dump(exclude_unset=True))
    )


@router.post("/returns", response_model=ApiResponse[dict], status_code=201)
async def create_return(
    payload: s.ReturnRequest,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(WRITE)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).record_return(payload.model_dump()))


@router.get("/returns", response_model=ApiResponse[dict])
async def list_returns(
    warehouse_id: uuid.UUID | None = None,
    party_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return _paged(
        await _svc(session, user).list_returns(
            warehouse_id=warehouse_id, party_id=party_id, date_from=date_from, date_to=date_to,
            page=page, page_size=page_size,
        )
    )


@router.get("/returns/{return_id}", response_model=ApiResponse[dict])
async def get_return(
    return_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return ApiResponse(data=await _svc(session, user).get_return(return_id))


# ------------------------------------------------------------------- ledger
@router.get("/ledger", response_model=ApiResponse[dict])
async def list_ledger(
    warehouse_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    movement_type: str | None = None,
    q: str | None = Query(default=None, max_length=120),
    category: str | None = None,
    reference_no: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
) -> ApiResponse[dict]:
    return _paged(
        await _svc(session, user).list_ledger(
            warehouse_id=warehouse_id, item_id=item_id, movement_type=movement_type, q=q,
            category=category, reference_no=reference_no, date_from=date_from, date_to=date_to,
            page=page, page_size=page_size,
        )
    )


# ------------------------------------------------------------------ reports
@router.get("/reports/{name}")
async def report(
    name: str,
    format: str = Query(default="json", pattern="^(json|xlsx)$"),
    view: str = "warehouse",
    as_of: date | None = None,
    warehouse_id: uuid.UUID | None = None,
    category: str | None = None,
    q: str | None = Query(default=None, max_length=120),
    date_from: date | None = None,
    date_to: date | None = None,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission(READ)),
):
    data = await ims_reports.build_report(
        _svc(session, user), name, view=view, as_of=as_of, warehouse_id=warehouse_id,
        category=category, q=q, date_from=date_from, date_to=date_to,
    )
    if format == "xlsx":
        return Response(
            content=ims_reports.to_xlsx(data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="ims-{name}-report.xlsx"'},
        )
    return ApiResponse(data=data)
