"""Inventory Management System (IMS) domain service.

Every stock change goes through `IMSService._move`, which (in the caller's
transaction) locks the (warehouse, item) balance row, refuses to go below
zero, updates the balance and appends an `ims_ledger` row carrying
before/after quantities. Balances are therefore always derivable from the
ledger, and history/"as of" reports sum the ledger, never today's balance.

Concurrency: balance rows are locked with `SELECT ... FOR UPDATE` in a
deterministic (warehouse, item) order so concurrent transfers cannot
deadlock or double-spend; the `quantity >= 0` CHECK is the final backstop.
Idempotency: each write carries a client key stored under a UNIQUE
constraint, so a double-click / retry replays the original record.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from fractions import Fraction
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, OMSError, ValidationError
from app.core.timezone import IST_OFFSET, ist_today
from app.models.auth import User
from app.models.inventory_architecture import Warehouse
from app.models.ims import (
    KIND_FINISHED,
    KIND_PACKAGING,
    PACKAGING_UNITS,
    ImsAdjustment,
    ImsBalance,
    ImsExternalParty,
    ImsItem,
    ImsLedger,
    ImsPackagingProduct,
    ImsReturn,
    ImsReturnLine,
    ImsTransfer,
    ImsTransferLine,
    ImsUnitConversion,
)
from app.services import ims_catalog
from app.services.audit_service import AuditService

MAX_QTY = 10**12
_ADJ_RULES = {
    "increase": ("in", "adjustment_in"),
    "decrease": ("out", "adjustment_out"),
    "damaged": ("out", "damaged"),
    "missing": ("out", "missing"),
}


# --------------------------------------------------------------------- utils
def ist_day_start_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC) - IST_OFFSET


def day_range_utc(date_from: date | None, date_to: date | None) -> tuple[datetime | None, ...]:
    """[start, end) UTC bounds covering the IST calendar days, inclusive."""
    start = ist_day_start_utc(date_from) if date_from else None
    end = ist_day_start_utc(date_to + timedelta(days=1)) if date_to else None
    return start, end


def new_reference(prefix: str) -> str:
    return f"{prefix}-{ist_today():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _check_qty(qty: int, what: str = "Quantity") -> int:
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        raise ValidationError(f"{what} must be a whole number greater than zero.")
    if qty > MAX_QTY:
        raise ValidationError(f"{what} is too large.")
    return qty


def stock_status(total: int, threshold: int | None) -> str:
    if total <= 0:
        return "out_of_stock"
    if threshold is not None and total <= threshold:
        return "low_stock"
    return "in_stock"


def effective_threshold(item: ImsItem) -> int | None:
    if item.low_stock_threshold is not None:
        return item.low_stock_threshold
    return settings.IMS_DEFAULT_LOW_STOCK_THRESHOLD if item.kind == KIND_FINISHED else None


def item_dict(item: ImsItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "kind": item.kind,
        "name": item.name,
        "sku": item.sku,
        "category": item.category,
        "unit": item.unit,
        "packaging_product_id": str(item.packaging_product_id)
        if item.packaging_product_id
        else None,
        "shopify_product_id": item.shopify_product_id,
        "shopify_variant_id": item.shopify_variant_id,
        "is_active": item.is_active,
        "low_stock_threshold": item.low_stock_threshold,
        "effective_low_stock_threshold": effective_threshold(item),
    }


def warehouse_dict(w: Warehouse) -> dict[str, Any]:
    return {
        "id": str(w.id),
        "code": w.code,
        "name": w.name,
        "address": w.address,
        "description": w.description,
        "is_active": w.is_active,
        "sort_order": w.sort_order,
    }


# ----------------------------------------------------------------- conversion
def _solve_units(rules: Iterable[tuple[str, str, int]]) -> dict[str, Fraction]:
    """Size of each connected unit relative to its component's root, e.g.
    BOX=4200, OUTER=60, SACHETS=1 (the smallest unit). Raises on a
    contradictory rule set."""
    graph: dict[str, list[tuple[str, Fraction]]] = defaultdict(list)
    for larger, smaller, ratio in rules:
        graph[larger].append((smaller, Fraction(1, ratio)))  # smaller = larger / ratio
        graph[smaller].append((larger, Fraction(ratio)))
    value: dict[str, Fraction] = {}
    for root in list(graph):
        if root in value:
            continue
        value[root] = Fraction(1)
        stack = [root]
        while stack:
            cur = stack.pop()
            for nxt, factor in graph[cur]:
                size = value[cur] * factor
                if nxt not in value:
                    value[nxt] = size
                    stack.append(nxt)
                elif value[nxt] != size:
                    raise ValidationError(
                        f"Conversion rules contradict each other around {cur} and {nxt}."
                    )
    return value


def _components(rules: list[tuple[str, str, int]]) -> list[set[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in rules:
        parent[find(a)] = find(b)
    comps: dict[str, set[str]] = defaultdict(set)
    for unit in list(parent):
        comps[find(unit)].add(unit)
    return list(comps.values())


def equivalence_text(rules: list[tuple[str, str, int]]) -> list[str]:
    """['1 BOX = 70 OUTER = 4,200 SACHETS'] -- one string per unit family."""
    if not rules:
        return []
    sizes = _solve_units(rules)
    out = []
    for comp in _components(rules):
        ordered = sorted(comp, key=lambda u: sizes[u], reverse=True)
        top = ordered[0]
        parts = [f"1 {top}"]
        for unit in ordered[1:]:
            n = sizes[top] / sizes[unit]
            parts.append(f"{int(n):,} {unit}" if n.denominator == 1 else f"{float(n):g} {unit}")
        out.append(" = ".join(parts))
    return out


# ---------------------------------------------------------------- the service
class IMSService:
    def __init__(self, session: AsyncSession, actor: User | None = None) -> None:
        self.session = session
        self.actor = actor

    # ------------------------------------------------------------ low level
    def _insert(self, model):  # noqa: ANN001
        name = self.session.get_bind().dialect.name
        return (postgresql.insert if name == "postgresql" else sqlite.insert)(model)

    async def _lock_balances(self, keys: Iterable[tuple[uuid.UUID, uuid.UUID]]) -> dict:
        """Create-if-missing then lock balance rows in a deterministic order."""
        ordered = sorted(set(keys), key=lambda k: (str(k[0]), str(k[1])))
        locked: dict[tuple[uuid.UUID, uuid.UUID], ImsBalance] = {}
        for wh_id, item_id in ordered:
            await self.session.execute(
                self._insert(ImsBalance)
                .values(id=uuid.uuid4(), warehouse_id=wh_id, item_id=item_id, quantity=0)
                .on_conflict_do_nothing(index_elements=["warehouse_id", "item_id"])
            )
            bal = (
                await self.session.execute(
                    select(ImsBalance)
                    .where(ImsBalance.warehouse_id == wh_id, ImsBalance.item_id == item_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            locked[(wh_id, item_id)] = bal
        return locked

    def _move(
        self,
        bal: ImsBalance,
        delta: int,
        movement_type: str,
        *,
        item: ImsItem,
        reference_no: str | None,
        occurred_at: datetime,
        notes: str | None = None,
        transfer_id: uuid.UUID | None = None,
        return_id: uuid.UUID | None = None,
        adjustment_id: uuid.UUID | None = None,
    ) -> ImsLedger:
        before = int(bal.quantity)
        after = before + delta
        if after < 0:
            raise ConflictError(
                f"Insufficient stock for {item.name}: available {before}, requested {-delta}.",
                details={
                    "item_id": str(item.id),
                    "item": item.name,
                    "available": before,
                    "requested": -delta,
                },
            )
        bal.quantity = after
        row = ImsLedger(
            movement_type=movement_type,
            warehouse_id=bal.warehouse_id,
            item_id=bal.item_id,
            quantity_delta=delta,
            quantity_before=before,
            quantity_after=after,
            reference_no=reference_no,
            transfer_id=transfer_id,
            return_id=return_id,
            adjustment_id=adjustment_id,
            actor_user_id=self.actor.id if self.actor else None,
            occurred_at=occurred_at,
            notes=notes,
        )
        self.session.add(row)
        return row

    async def _warehouse(self, wh_id: uuid.UUID, *, must_be_active: bool = True) -> Warehouse:
        wh = (
            await self.session.execute(
                select(Warehouse).where(Warehouse.id == wh_id, Warehouse.is_ims.is_(True))
            )
        ).scalar_one_or_none()
        if wh is None:
            raise NotFoundError("Warehouse not found.")
        if must_be_active and not wh.is_active:
            raise ValidationError(f"Warehouse {wh.name} ({wh.code}) is not active.")
        return wh

    async def _items(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, ImsItem]:
        ids = list(set(ids))
        rows = (
            (await self.session.execute(select(ImsItem).where(ImsItem.id.in_(ids))))
            .scalars()
            .all()
        )
        found = {i.id: i for i in rows}
        for i in ids:
            if i not in found:
                raise NotFoundError("Inventory item not found.")
            if not found[i].is_active:
                raise ValidationError(f"{found[i].name} is inactive.")
        return found

    async def _guard(self, coro_fn):  # noqa: ANN001
        """Run a write; any domain error rolls the whole transaction back."""
        try:
            return await coro_fn()
        except (OMSError, ValueError):
            await self.session.rollback()
            raise
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("The operation conflicted with existing data.") from exc

    async def _audit(self, action: str, entity_type: str, entity_id: Any, data: dict) -> None:
        await AuditService(self.session).record(
            user=self.actor,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            new_value=data,
        )

    # ----------------------------------------------------------- warehouses
    async def list_warehouses(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        stmt = select(Warehouse).where(Warehouse.is_ims.is_(True)).order_by(
            Warehouse.sort_order, Warehouse.code
        )
        if active_only:
            stmt = stmt.where(Warehouse.is_active.is_(True))
        return [warehouse_dict(w) for w in (await self.session.execute(stmt)).scalars().all()]

    async def create_warehouse(self, data: dict[str, Any]) -> dict[str, Any]:
        code, name = (data["code"] or "").strip(), (data["name"] or "").strip()
        if not code or not name:
            raise ValidationError("Warehouse name and code are required.")
        clash = (
            await self.session.execute(select(Warehouse).where(Warehouse.code == code))
        ).scalar_one_or_none()
        if clash is not None:
            raise ConflictError(f"A warehouse with code {code} already exists.")
        next_order = (
            await self.session.execute(
                select(func.coalesce(func.max(Warehouse.sort_order), 0)).where(
                    Warehouse.is_ims.is_(True)
                )
            )
        ).scalar_one() + 1
        wh = Warehouse(
            code=code,
            name=name,
            address=_clean(data.get("address")),
            description=_clean(data.get("description")),
            is_active=data.get("is_active", True),
            sort_order=data.get("sort_order") or next_order,
            is_ims=True,
            is_default=False,
        )
        self.session.add(wh)
        await self.session.flush()
        await self._audit("ims.warehouse_created", "warehouse", wh.id, warehouse_dict(wh))
        await self.session.commit()
        return warehouse_dict(wh)

    async def update_warehouse(self, wh_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        wh = await self._warehouse(wh_id, must_be_active=False)
        if "code" in data and data["code"] is not None:
            code = data["code"].strip()
            if not code:
                raise ValidationError("Warehouse code cannot be empty.")
            clash = (
                await self.session.execute(
                    select(Warehouse).where(Warehouse.code == code, Warehouse.id != wh.id)
                )
            ).scalar_one_or_none()
            if clash is not None:
                raise ConflictError(f"A warehouse with code {code} already exists.")
            wh.code = code
        if data.get("name") is not None:
            if not data["name"].strip():
                raise ValidationError("Warehouse name cannot be empty.")
            wh.name = data["name"].strip()
        for field in ("address", "description"):
            if field in data:
                setattr(wh, field, _clean(data[field]))
        if data.get("is_active") is not None:
            wh.is_active = data["is_active"]
        if data.get("sort_order") is not None:
            wh.sort_order = data["sort_order"]
        await self._audit("ims.warehouse_updated", "warehouse", wh.id, warehouse_dict(wh))
        await self.session.commit()
        return warehouse_dict(wh)

    # ----------------------------------------------------------------- items
    async def resolve_finished_item(
        self, *, product_id: str, variant_id: str
    ) -> dict[str, Any]:
        """Get-or-create the IMS identity row for a Shopify variant."""
        existing = (
            await self.session.execute(
                select(ImsItem).where(ImsItem.shopify_variant_id == variant_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.shopify_product_id != product_id:
                raise NotFoundError("That variant does not belong to the given product.")
            return item_dict(existing)
        snap = await ims_catalog.get_catalog()
        hit = ims_catalog.find_variant(snap.products, variant_id)
        if hit is None or hit[0]["product_id"] != product_id:
            raise NotFoundError("That variant was not found in the Shopify catalogue.")
        product, variant = hit
        item = ImsItem(
            kind=KIND_FINISHED,
            name=ims_catalog.variant_label(product, variant),
            sku=variant["sku"],
            category=product["category"],
            shopify_product_id=product_id,
            shopify_variant_id=variant_id,
        )
        self.session.add(item)
        try:
            await self.session.flush()
        except IntegrityError:  # lost a race with another registration
            await self.session.rollback()
            existing = (
                await self.session.execute(
                    select(ImsItem).where(ImsItem.shopify_variant_id == variant_id)
                )
            ).scalar_one()
            return item_dict(existing)
        await self.session.commit()
        return item_dict(item)

    async def update_item(self, item_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        item = (
            await self.session.execute(select(ImsItem).where(ImsItem.id == item_id))
        ).scalar_one_or_none()
        if item is None:
            raise NotFoundError("Inventory item not found.")
        if "low_stock_threshold" in data:
            t = data["low_stock_threshold"]
            if t is not None and t < 0:
                raise ValidationError("Low-stock threshold cannot be negative.")
            item.low_stock_threshold = t
        if data.get("is_active") is not None:
            item.is_active = data["is_active"]
        await self._audit("ims.item_updated", "ims_item", item.id, data)
        await self.session.commit()
        return item_dict(item)

    # ------------------------------------------------------------- packaging
    async def create_packaging_product(self, data: dict[str, Any]) -> dict[str, Any]:
        name = (data["name"] or "").strip()
        if not name:
            raise ValidationError("Product name is required.")
        units = list(dict.fromkeys(data.get("units") or []))
        self._check_units(units)
        if (
            await self.session.execute(
                select(ImsPackagingProduct).where(ImsPackagingProduct.name == name)
            )
        ).scalar_one_or_none():
            raise ConflictError(f"Packaging product '{name}' already exists.")
        product = ImsPackagingProduct(
            name=name,
            category=_clean(data.get("category")),
            shopify_product_id=_clean(data.get("shopify_product_id")),
            notes=_clean(data.get("notes")),
        )
        self.session.add(product)
        await self.session.flush()
        for unit in units:
            self._new_unit_item(product, unit)
        await self.session.flush()
        await self._audit("ims.packaging_product_created", "ims_packaging_product", product.id,
                          {"name": name, "units": units})
        await self.session.commit()
        return await self.get_packaging_product(product.id)

    @staticmethod
    def _check_units(units: Iterable[str]) -> None:
        for u in units:
            if u not in PACKAGING_UNITS:
                raise ValidationError(
                    f"Unknown packaging unit '{u}'. Allowed: {', '.join(PACKAGING_UNITS)}."
                )

    def _new_unit_item(self, product: ImsPackagingProduct, unit: str) -> ImsItem:
        item = ImsItem(
            kind=KIND_PACKAGING,
            name=f"{product.name} — {unit}",
            category=product.category,
            packaging_product_id=product.id,
            unit=unit,
        )
        self.session.add(item)
        return item

    async def update_packaging_product(
        self, product_id: uuid.UUID, data: dict[str, Any]
    ) -> dict[str, Any]:
        product = await self._pkg_product(product_id)
        if data.get("name") is not None:
            new_name = data["name"].strip()
            if not new_name:
                raise ValidationError("Product name cannot be empty.")
            if new_name != product.name:
                clash = (
                    await self.session.execute(
                        select(ImsPackagingProduct).where(ImsPackagingProduct.name == new_name)
                    )
                ).scalar_one_or_none()
                if clash:
                    raise ConflictError(f"Packaging product '{new_name}' already exists.")
                product.name = new_name
        for field in ("category", "notes", "shopify_product_id"):
            if field in data:
                setattr(product, field, _clean(data[field]))
        if data.get("is_active") is not None:
            product.is_active = data["is_active"]
        add_units = [u for u in data.get("add_units") or []]
        self._check_units(add_units)
        existing = {
            i.unit: i
            for i in (
                await self.session.execute(
                    select(ImsItem).where(ImsItem.packaging_product_id == product.id)
                )
            ).scalars()
        }
        for unit in add_units:
            if unit not in existing:
                self._new_unit_item(product, unit)
        for unit, item in existing.items():
            item.name = f"{product.name} — {unit}"
            item.category = product.category
            if data.get("is_active") is not None:
                item.is_active = data["is_active"]
        await self._audit("ims.packaging_product_updated", "ims_packaging_product", product.id,
                          {k: v for k, v in data.items()})
        await self.session.commit()
        return await self.get_packaging_product(product.id)

    async def _pkg_product(self, product_id: uuid.UUID) -> ImsPackagingProduct:
        product = (
            await self.session.execute(
                select(ImsPackagingProduct).where(ImsPackagingProduct.id == product_id)
            )
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("Packaging product not found.")
        return product

    async def set_conversions(
        self, product_id: uuid.UUID, rules: list[dict[str, Any]]
    ) -> dict[str, Any]:
        product = await self._pkg_product(product_id)
        parsed: list[tuple[str, str, int]] = []
        seen: set[tuple[str, str]] = set()
        for r in rules:
            larger, smaller, ratio = r["larger_unit"], r["smaller_unit"], r["ratio"]
            self._check_units([larger, smaller])
            if larger == smaller:
                raise ValidationError("A unit cannot be converted to itself.")
            if not isinstance(ratio, int) or isinstance(ratio, bool) or ratio < 1:
                raise ValidationError("Conversion ratio must be a whole number of at least 1.")
            pair = tuple(sorted((larger, smaller)))
            if pair in seen:
                raise ValidationError(f"Duplicate conversion for {larger} / {smaller}.")
            seen.add(pair)
            parsed.append((larger, smaller, ratio))
        _solve_units(parsed)  # raises on contradictions
        await self.session.execute(
            delete(ImsUnitConversion).where(ImsUnitConversion.packaging_product_id == product.id)
        )
        for larger, smaller, ratio in parsed:
            self.session.add(
                ImsUnitConversion(
                    packaging_product_id=product.id,
                    larger_unit=larger,
                    smaller_unit=smaller,
                    ratio=ratio,
                )
            )
        await self._audit(
            "ims.conversions_updated", "ims_packaging_product", product.id,
            {"rules": [{"larger": a, "smaller": b, "ratio": c} for a, b, c in parsed]},
        )
        await self.session.commit()
        return await self.get_packaging_product(product.id)

    async def convert(
        self, product_id: uuid.UUID, from_unit: str, to_unit: str, quantity: int
    ) -> dict[str, Any]:
        """Pure arithmetic over the product's rules. Records nothing and
        never consumes or produces stock."""
        await self._pkg_product(product_id)
        _check_qty(quantity)
        self._check_units([from_unit, to_unit])
        rules = await self._rules(product_id)
        sizes = _solve_units(rules)
        if from_unit not in sizes or to_unit not in sizes:
            raise ValidationError(f"No conversion is configured between {from_unit} and {to_unit}.")
        if from_unit != to_unit:
            comps = _components(rules)
            if not any(from_unit in c and to_unit in c for c in comps):
                raise ValidationError(
                    f"No conversion is configured between {from_unit} and {to_unit}."
                )
        exact = Fraction(quantity) * sizes[from_unit] / sizes[to_unit]
        whole = exact.numerator // exact.denominator
        remainder_in_from = (
            quantity - int(Fraction(whole) * sizes[to_unit] / sizes[from_unit])
            if exact.denominator != 1
            else 0
        )
        return {
            "from_unit": from_unit,
            "to_unit": to_unit,
            "quantity": quantity,
            "result": whole if exact.denominator == 1 else float(exact),
            "is_whole_number": exact.denominator == 1,
            "whole_result": whole,
            "remainder_in_from_unit": remainder_in_from,
            "note": "Display conversion only; no stock was created or consumed.",
        }

    async def _rules(self, product_id: uuid.UUID) -> list[tuple[str, str, int]]:
        rows = (
            await self.session.execute(
                select(ImsUnitConversion).where(ImsUnitConversion.packaging_product_id == product_id)
            )
        ).scalars()
        return [(r.larger_unit, r.smaller_unit, r.ratio) for r in rows]

    async def get_packaging_product(
        self, product_id: uuid.UUID, warehouse_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        rows = await self.list_packaging_products(warehouse_id=warehouse_id, product_id=product_id)
        if not rows:
            raise NotFoundError("Packaging product not found.")
        return rows[0]

    async def list_packaging_products(
        self,
        *,
        warehouse_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        q: str | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        stmt = select(ImsPackagingProduct).order_by(ImsPackagingProduct.name)
        if product_id:
            stmt = stmt.where(ImsPackagingProduct.id == product_id)
        if q:
            stmt = stmt.where(ImsPackagingProduct.name.ilike(f"%{q.strip()}%"))
        if not include_inactive and not product_id:
            stmt = stmt.where(ImsPackagingProduct.is_active.is_(True))
        products = (await self.session.execute(stmt)).scalars().all()
        if not products:
            return []
        ids = [p.id for p in products]
        items = (
            (
                await self.session.execute(
                    select(ImsItem).where(ImsItem.packaging_product_id.in_(ids))
                )
            )
            .scalars()
            .all()
        )
        qty = await self._qty_by_item(warehouse_id=warehouse_id, item_ids=[i.id for i in items])
        per_wh = await self._qty_by_item_wh([i.id for i in items])
        conv = (
            (
                await self.session.execute(
                    select(ImsUnitConversion).where(
                        ImsUnitConversion.packaging_product_id.in_(ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        whs = {w["id"]: w for w in await self.list_warehouses()}
        out = []
        for p in products:
            rules = [(c.larger_unit, c.smaller_unit, c.ratio) for c in conv
                     if c.packaging_product_id == p.id]
            p_items = [i for i in items if i.packaging_product_id == p.id]
            units = {}
            for i in sorted(p_items, key=lambda x: PACKAGING_UNITS.index(x.unit)):
                units[i.unit] = {
                    "item_id": str(i.id),
                    "quantity": qty.get(i.id, 0),
                    "low_stock_threshold": i.low_stock_threshold,
                    "is_active": i.is_active,
                    "by_warehouse": [
                        {
                            "warehouse_id": wid,
                            "warehouse": whs[wid]["name"],
                            "code": whs[wid]["code"],
                            "quantity": per_wh.get((i.id, uuid.UUID(wid)), 0),
                        }
                        for wid in whs
                    ],
                }
            out.append(
                {
                    "id": str(p.id),
                    "name": p.name,
                    "category": p.category,
                    "shopify_product_id": p.shopify_product_id,
                    "is_active": p.is_active,
                    "notes": p.notes,
                    "tracked_units": list(units),
                    "units": units,
                    "conversions": [
                        {"larger_unit": a, "smaller_unit": b, "ratio": r} for a, b, r in rules
                    ],
                    "conversion_summary": equivalence_text(rules),
                }
            )
        return out

    # -------------------------------------------------------- balances (read)
    async def _qty_by_item(
        self, *, warehouse_id: uuid.UUID | None, item_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if not item_ids:
            return {}
        stmt = (
            select(ImsBalance.item_id, func.sum(ImsBalance.quantity))
            .join(Warehouse, Warehouse.id == ImsBalance.warehouse_id)
            .where(ImsBalance.item_id.in_(item_ids), Warehouse.is_ims.is_(True))
            .group_by(ImsBalance.item_id)
        )
        if warehouse_id:
            stmt = stmt.where(ImsBalance.warehouse_id == warehouse_id)
        return {r[0]: int(r[1] or 0) for r in (await self.session.execute(stmt)).all()}

    async def _qty_by_item_wh(self, item_ids: list[uuid.UUID]) -> dict[tuple, int]:
        if not item_ids:
            return {}
        rows = await self.session.execute(
            select(ImsBalance.item_id, ImsBalance.warehouse_id, ImsBalance.quantity).where(
                ImsBalance.item_id.in_(item_ids)
            )
        )
        return {(r[0], r[1]): int(r[2]) for r in rows.all()}

    async def available(self, warehouse_id: uuid.UUID, item_id: uuid.UUID) -> int:
        row = (
            await self.session.execute(
                select(ImsBalance.quantity).where(
                    ImsBalance.warehouse_id == warehouse_id, ImsBalance.item_id == item_id
                )
            )
        ).scalar_one_or_none()
        return int(row or 0)

    async def search_items(
        self,
        *,
        q: str | None = None,
        kind: str | None = None,
        warehouse_id: uuid.UUID | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        stmt = select(ImsItem).where(ImsItem.is_active.is_(True)).order_by(ImsItem.name).limit(limit)
        if kind:
            stmt = stmt.where(ImsItem.kind == kind)
        if q and q.strip():
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(ImsItem.name.ilike(like), ImsItem.sku.ilike(like)))
        items = (await self.session.execute(stmt)).scalars().all()
        qty = await self._qty_by_item(warehouse_id=warehouse_id, item_ids=[i.id for i in items])
        return [{**item_dict(i), "available": qty.get(i.id, 0)} for i in items]

    async def stock_overview(
        self,
        *,
        warehouse_id: uuid.UUID | None = None,
        kind: str | None = None,
        category: str | None = None,
        q: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        """One row per item, with its quantity in every IMS warehouse."""
        stmt = select(ImsItem).where(ImsItem.is_active.is_(True))
        if kind:
            stmt = stmt.where(ImsItem.kind == kind)
        if category:
            stmt = stmt.where(ImsItem.category == category)
        if q and q.strip():
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(ImsItem.name.ilike(like), ImsItem.sku.ilike(like)))
        items = (await self.session.execute(stmt.order_by(ImsItem.name))).scalars().all()
        per_wh = await self._qty_by_item_wh([i.id for i in items])
        whs = await self.list_warehouses()
        rows = []
        for i in items:
            by_wh = {w["id"]: per_wh.get((i.id, uuid.UUID(w["id"])), 0) for w in whs}
            total = by_wh[str(warehouse_id)] if warehouse_id else sum(by_wh.values())
            st = stock_status(total, effective_threshold(i))
            if status and st != status:
                continue
            rows.append({**item_dict(i), "by_warehouse": by_wh, "total": total, "status": st})
        total_items = len(rows)
        start = (page - 1) * page_size
        return {
            "items": rows[start : start + page_size],
            "total_items": total_items,
            "page": page,
            "page_size": page_size,
            "warehouses": whs,
        }

    async def item_detail(self, item_id: uuid.UUID) -> dict[str, Any]:
        item = (
            await self.session.execute(select(ImsItem).where(ImsItem.id == item_id))
        ).scalar_one_or_none()
        if item is None:
            raise NotFoundError("Inventory item not found.")
        per_wh = await self._qty_by_item_wh([item.id])
        whs = await self.list_warehouses()
        by_wh = [
            {**w, "quantity": per_wh.get((item.id, uuid.UUID(w["id"])), 0)} for w in whs
        ]
        total = sum(w["quantity"] for w in by_wh)
        data = {
            **item_dict(item),
            "by_warehouse": by_wh,
            "total": total,
            "status": stock_status(total, effective_threshold(item)),
        }
        if item.packaging_product_id:
            data["packaging_product"] = await self.get_packaging_product(item.packaging_product_id)
        elif item.shopify_product_id:
            siblings = (
                (
                    await self.session.execute(
                        select(ImsItem).where(
                            ImsItem.shopify_product_id == item.shopify_product_id,
                            ImsItem.id != item.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            data["sibling_variants"] = [item_dict(s) for s in siblings]
        return data

    # ------------------------------------------------------------ operations
    async def _find_replay(self, model, key: str):  # noqa: ANN001
        return (
            await self.session.execute(select(model).where(model.idempotency_key == key))
        ).scalar_one_or_none()

    async def stock_in(self, data: dict[str, Any]) -> dict[str, Any]:
        """Stock inward (e.g. new packaging received)."""
        return await self._single_adjustment(data, kind="inward")

    async def adjust(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._single_adjustment(data, kind="adjustment")

    async def _single_adjustment(self, data: dict[str, Any], *, kind: str) -> dict[str, Any]:
        async def run() -> dict[str, Any]:
            key = data["idempotency_key"]
            replay = await self._find_replay(ImsAdjustment, key)
            if replay is not None:
                return {**await self._adjustment_dict(replay), "replayed": True}
            qty = _check_qty(data["quantity"])
            reason = (data.get("reason") or "").strip()
            if len(reason) < 3:
                raise ValidationError("A reason is required (at least 3 characters).")
            if kind == "inward":
                adj_type, direction, mtype = "increase", "in", "inward"
            else:
                adj_type = data["adjustment_type"]
                if adj_type in _ADJ_RULES:
                    direction, mtype = _ADJ_RULES[adj_type]
                    given = data.get("direction")
                    if given and given != direction:
                        raise ValidationError(
                            f"A '{adj_type}' adjustment is always '{direction}'; got '{given}'."
                        )
                else:  # count_correction / other
                    direction = data.get("direction")
                    if direction not in ("in", "out"):
                        raise ValidationError("Direction (in/out) is required for this type.")
                    mtype = "adjustment_in" if direction == "in" else "adjustment_out"
            wh = await self._warehouse(data["warehouse_id"])
            item = (await self._items([data["item_id"]]))[data["item_id"]]
            adj = ImsAdjustment(
                reference_no=new_reference("INW" if kind == "inward" else "ADJ"),
                idempotency_key=key,
                warehouse_id=wh.id,
                item_id=item.id,
                adjustment_type=adj_type,
                direction=direction,
                quantity=qty,
                reason=reason[:500],
                notes=_clean(data.get("notes")),
                created_by=self.actor.id if self.actor else None,
            )
            self.session.add(adj)
            try:
                await self.session.flush()
            except IntegrityError:
                await self.session.rollback()
                replay = await self._find_replay(ImsAdjustment, key)
                if replay is None:
                    raise
                return {**await self._adjustment_dict(replay), "replayed": True}
            bal = (await self._lock_balances([(wh.id, item.id)]))[(wh.id, item.id)]
            self._move(
                bal, qty if direction == "in" else -qty, mtype, item=item,
                reference_no=adj.reference_no, occurred_at=datetime.now(UTC),
                notes=f"{reason}" + (f" | {adj.notes}" if adj.notes else ""),
                adjustment_id=adj.id,
            )
            await self._audit("ims.stock_adjusted" if kind == "adjustment" else "ims.stock_in",
                              "ims_adjustment", adj.id,
                              {"reference_no": adj.reference_no, "item": item.name,
                               "warehouse": wh.code, "type": adj_type, "direction": direction,
                               "quantity": qty, "reason": reason})
            await self.session.commit()
            return {**await self._adjustment_dict(adj), "replayed": False}

        return await self._guard(run)

    async def opening_balances(self, data: dict[str, Any]) -> dict[str, Any]:
        """Load verified opening stock. Only for (warehouse, item) pairs that
        have no movements yet, so it can never corrupt existing inventory."""

        async def run() -> dict[str, Any]:
            as_of: date = data["as_of"]
            if as_of > ist_today():
                raise ValidationError("The opening-balance date cannot be in the future.")
            lines = data["lines"]
            if not lines:
                raise ValidationError("Provide at least one line.")
            pairs = [(l["warehouse_id"], l["item_id"]) for l in lines]
            if len(set(pairs)) != len(pairs):
                raise ValidationError("Each warehouse/item pair may appear only once.")
            items = await self._items([l["item_id"] for l in lines])
            for wid in {p[0] for p in pairs}:
                await self._warehouse(wid)
            ref = new_reference("OPEN")
            locked = await self._lock_balances(pairs)
            for line in lines:
                key = (line["warehouse_id"], line["item_id"])
                used = (
                    await self.session.execute(
                        select(func.count()).select_from(ImsLedger).where(
                            ImsLedger.warehouse_id == key[0], ImsLedger.item_id == key[1]
                        )
                    )
                ).scalar_one()
                if used:
                    raise ConflictError(
                        f"{items[key[1]].name} already has stock history in that warehouse; "
                        "use a stock adjustment instead."
                    )
                self._move(
                    locked[key], _check_qty(line["quantity"]), "opening_balance",
                    item=items[key[1]], reference_no=ref,
                    occurred_at=ist_day_start_utc(as_of), notes=_clean(data.get("notes")),
                )
            await self._audit("ims.opening_balances_loaded", "ims_ledger", ref,
                              {"as_of": str(as_of), "lines": len(lines)})
            await self.session.commit()
            return {"reference_no": ref, "lines": len(lines), "as_of": str(as_of)}

        return await self._guard(run)

    async def transfer(self, data: dict[str, Any]) -> dict[str, Any]:
        async def run() -> dict[str, Any]:
            key = data["idempotency_key"]
            replay = await self._find_replay(ImsTransfer, key)
            if replay is not None:
                return {**await self.get_transfer(replay.id), "replayed": True}
            src_id, dst_id = data["source_warehouse_id"], data["destination_warehouse_id"]
            if src_id == dst_id:
                raise ValidationError("Source and destination warehouses must be different.")
            src, dst = await self._warehouse(src_id), await self._warehouse(dst_id)
            lines = data["lines"]
            if not lines:
                raise ValidationError("Add at least one item to transfer.")
            ids = [l["item_id"] for l in lines]
            if len(set(ids)) != len(ids):
                raise ValidationError("An item can appear only once per transfer.")
            items = await self._items(ids)
            for l in lines:
                _check_qty(l["quantity"])

            transfer = ImsTransfer(
                reference_no=new_reference("TRF"),
                idempotency_key=key,
                source_warehouse_id=src.id,
                destination_warehouse_id=dst.id,
                notes=_clean(data.get("notes")),
                created_by=self.actor.id if self.actor else None,
            )
            self.session.add(transfer)
            try:
                await self.session.flush()
            except IntegrityError:
                await self.session.rollback()
                replay = await self._find_replay(ImsTransfer, key)
                if replay is None:
                    raise
                return {**await self.get_transfer(replay.id), "replayed": True}
            for l in lines:
                self.session.add(
                    ImsTransferLine(transfer_id=transfer.id, item_id=l["item_id"], quantity=l["quantity"])
                )
            keys = [(src.id, i) for i in ids] + [(dst.id, i) for i in ids]
            locked = await self._lock_balances(keys)
            now = datetime.now(UTC)
            for l in lines:
                item, qty = items[l["item_id"]], l["quantity"]
                common = dict(
                    item=item, reference_no=transfer.reference_no, occurred_at=now,
                    notes=transfer.notes, transfer_id=transfer.id,
                )
                self._move(locked[(src.id, item.id)], -qty, "transfer_out", **common)
                self._move(locked[(dst.id, item.id)], qty, "transfer_in", **common)
            await self._audit("ims.stock_transferred", "ims_transfer", transfer.id,
                              {"reference_no": transfer.reference_no, "from": src.code,
                               "to": dst.code, "lines": len(lines)})
            await self.session.commit()
            return {**await self.get_transfer(transfer.id), "replayed": False}

        return await self._guard(run)

    async def record_return(self, data: dict[str, Any]) -> dict[str, Any]:
        async def run() -> dict[str, Any]:
            key = data["idempotency_key"]
            replay = await self._find_replay(ImsReturn, key)
            if replay is not None:
                return {**await self.get_return(replay.id), "replayed": True}
            wh = await self._warehouse(data["warehouse_id"])
            party = (
                await self.session.execute(
                    select(ImsExternalParty).where(ImsExternalParty.id == data["party_id"])
                )
            ).scalar_one_or_none()
            if party is None:
                raise NotFoundError("External party not found.")
            if not party.is_active:
                raise ValidationError(f"{party.name} is inactive.")
            return_date: date = data["return_date"]
            today = ist_today()
            if return_date > today:
                raise ValidationError("The return date cannot be in the future.")
            lines = data["lines"]
            if not lines:
                raise ValidationError("Add at least one returned item.")
            ids = [l["item_id"] for l in lines]
            if len(set(ids)) != len(ids):
                raise ValidationError("An item can appear only once per return.")
            items = await self._items(ids)
            for l in lines:
                _check_qty(l["quantity"])
            occurred = (
                datetime.now(UTC)
                if return_date == today
                else ist_day_start_utc(return_date) + timedelta(hours=12)
            )
            ret = ImsReturn(
                reference_no=new_reference("RET"),
                idempotency_key=key,
                warehouse_id=wh.id,
                party_id=party.id,
                return_date=return_date,
                document_no=_clean(data.get("document_no")),
                notes=_clean(data.get("notes")),
                created_by=self.actor.id if self.actor else None,
            )
            self.session.add(ret)
            try:
                await self.session.flush()
            except IntegrityError:
                await self.session.rollback()
                replay = await self._find_replay(ImsReturn, key)
                if replay is None:
                    raise
                return {**await self.get_return(replay.id), "replayed": True}
            for l in lines:
                self.session.add(
                    ImsReturnLine(return_id=ret.id, item_id=l["item_id"], quantity=l["quantity"])
                )
            locked = await self._lock_balances([(wh.id, i) for i in ids])
            for l in lines:
                item = items[l["item_id"]]
                self._move(
                    locked[(wh.id, item.id)], l["quantity"], "return_in", item=item,
                    reference_no=ret.reference_no, occurred_at=occurred,
                    notes=f"Return from {party.name}"
                    + (f" | doc {ret.document_no}" if ret.document_no else ""),
                    return_id=ret.id,
                )
            await self._audit("ims.stock_returned", "ims_return", ret.id,
                              {"reference_no": ret.reference_no, "party": party.name,
                               "warehouse": wh.code, "lines": len(lines)})
            await self.session.commit()
            return {**await self.get_return(ret.id), "replayed": False}

        return await self._guard(run)

    # --------------------------------------------------------- external party
    async def list_parties(self, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        stmt = select(ImsExternalParty).order_by(ImsExternalParty.name)
        if not include_inactive:
            stmt = stmt.where(ImsExternalParty.is_active.is_(True))
        return [self._party(p) for p in (await self.session.execute(stmt)).scalars()]

    @staticmethod
    def _party(p: ImsExternalParty) -> dict[str, Any]:
        return {"id": str(p.id), "name": p.name, "notes": p.notes, "is_active": p.is_active}

    async def create_party(self, data: dict[str, Any]) -> dict[str, Any]:
        name = (data["name"] or "").strip()
        if not name:
            raise ValidationError("Party name is required.")
        if (
            await self.session.execute(
                select(ImsExternalParty).where(func.lower(ImsExternalParty.name) == name.lower())
            )
        ).scalar_one_or_none():
            raise ConflictError(f"'{name}' already exists.")
        party = ImsExternalParty(name=name, notes=_clean(data.get("notes")))
        self.session.add(party)
        await self.session.flush()
        await self._audit("ims.party_created", "ims_external_party", party.id, {"name": name})
        await self.session.commit()
        return self._party(party)

    async def update_party(self, party_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
        party = (
            await self.session.execute(
                select(ImsExternalParty).where(ImsExternalParty.id == party_id)
            )
        ).scalar_one_or_none()
        if party is None:
            raise NotFoundError("External party not found.")
        if data.get("name") is not None and data["name"].strip():
            party.name = data["name"].strip()
        if "notes" in data:
            party.notes = _clean(data["notes"])
        if data.get("is_active") is not None:
            party.is_active = data["is_active"]
        await self._audit("ims.party_updated", "ims_external_party", party.id, data)
        await self.session.commit()
        return self._party(party)

    # ----------------------------------------------------- history & records
    async def _names(self, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not user_ids:
            return {}
        rows = await self.session.execute(select(User.id, User.name).where(User.id.in_(user_ids)))
        return {r[0]: r[1] for r in rows.all()}

    async def _wh_map(self) -> dict[uuid.UUID, Warehouse]:
        rows = (
            await self.session.execute(select(Warehouse).where(Warehouse.is_ims.is_(True)))
        ).scalars()
        return {w.id: w for w in rows}

    def _wh_label(self, whs: dict, wid: uuid.UUID) -> dict[str, Any]:
        w = whs.get(wid)
        return {"id": str(wid), "name": w.name if w else None, "code": w.code if w else None}

    async def _adjustment_dict(self, a: ImsAdjustment) -> dict[str, Any]:
        whs, names = await self._wh_map(), await self._names({a.created_by} - {None})
        item = (await self.session.execute(select(ImsItem).where(ImsItem.id == a.item_id))).scalar_one()
        return {
            "id": str(a.id),
            "reference_no": a.reference_no,
            "warehouse": self._wh_label(whs, a.warehouse_id),
            "item": item_dict(item),
            "adjustment_type": a.adjustment_type,
            "direction": a.direction,
            "quantity": a.quantity,
            "reason": a.reason,
            "notes": a.notes,
            "created_by": names.get(a.created_by),
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }

    async def list_adjustments(
        self, *, warehouse_id=None, item_id=None, adjustment_type=None,  # noqa: ANN001
        date_from: date | None = None, date_to: date | None = None, page=1, page_size=25,
    ) -> dict[str, Any]:
        conds = []
        if warehouse_id:
            conds.append(ImsAdjustment.warehouse_id == warehouse_id)
        if item_id:
            conds.append(ImsAdjustment.item_id == item_id)
        if adjustment_type:
            conds.append(ImsAdjustment.adjustment_type == adjustment_type)
        start, end = day_range_utc(date_from, date_to)
        if start:
            conds.append(ImsAdjustment.created_at >= start)
        if end:
            conds.append(ImsAdjustment.created_at < end)
        total = (
            await self.session.execute(select(func.count()).select_from(ImsAdjustment).where(*conds))
        ).scalar_one()
        rows = (
            (
                await self.session.execute(
                    select(ImsAdjustment)
                    .where(*conds)
                    .order_by(ImsAdjustment.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        return {
            "items": [await self._adjustment_dict(a) for a in rows],
            "total_items": total, "page": page, "page_size": page_size,
        }

    async def _transfer_dict(self, t: ImsTransfer, whs: dict, names: dict) -> dict[str, Any]:
        return {
            "id": str(t.id),
            "reference_no": t.reference_no,
            "source": self._wh_label(whs, t.source_warehouse_id),
            "destination": self._wh_label(whs, t.destination_warehouse_id),
            "notes": t.notes,
            "created_by": names.get(t.created_by),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "lines": [{"item": item_dict(l.item), "quantity": l.quantity} for l in t.lines],
            "total_quantity": sum(l.quantity for l in t.lines),
        }

    async def get_transfer(self, transfer_id: uuid.UUID) -> dict[str, Any]:
        t = (
            await self.session.execute(
                select(ImsTransfer)
                .where(ImsTransfer.id == transfer_id)
                .options(selectinload(ImsTransfer.lines).selectinload(ImsTransferLine.item))
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if t is None:
            raise NotFoundError("Transfer not found.")
        data = await self._transfer_dict(t, await self._wh_map(), await self._names({t.created_by} - {None}))
        ledger = (
            (
                await self.session.execute(
                    select(ImsLedger)
                    .where(ImsLedger.transfer_id == t.id)
                    .order_by(ImsLedger.created_at, ImsLedger.id)
                )
            )
            .scalars()
            .all()
        )
        data["movements"] = [
            {
                "movement_type": m.movement_type,
                "warehouse_id": str(m.warehouse_id),
                "item_id": str(m.item_id),
                "quantity_delta": m.quantity_delta,
                "quantity_before": m.quantity_before,
                "quantity_after": m.quantity_after,
            }
            for m in ledger
        ]
        return data

    async def list_transfers(
        self, *, warehouse_id=None, item_id=None, q: str | None = None,  # noqa: ANN001
        date_from: date | None = None, date_to: date | None = None, page=1, page_size=25,
    ) -> dict[str, Any]:
        conds = []
        if warehouse_id:
            conds.append(
                or_(
                    ImsTransfer.source_warehouse_id == warehouse_id,
                    ImsTransfer.destination_warehouse_id == warehouse_id,
                )
            )
        if item_id:
            conds.append(
                ImsTransfer.id.in_(
                    select(ImsTransferLine.transfer_id).where(ImsTransferLine.item_id == item_id)
                )
            )
        if q and q.strip():
            like = f"%{q.strip()}%"
            conds.append(
                or_(
                    ImsTransfer.reference_no.ilike(like),
                    ImsTransfer.id.in_(
                        select(ImsTransferLine.transfer_id)
                        .join(ImsItem, ImsItem.id == ImsTransferLine.item_id)
                        .where(or_(ImsItem.name.ilike(like), ImsItem.sku.ilike(like)))
                    ),
                )
            )
        start, end = day_range_utc(date_from, date_to)
        if start:
            conds.append(ImsTransfer.created_at >= start)
        if end:
            conds.append(ImsTransfer.created_at < end)
        total = (
            await self.session.execute(select(func.count()).select_from(ImsTransfer).where(*conds))
        ).scalar_one()
        rows = (
            (
                await self.session.execute(
                    select(ImsTransfer)
                    .where(*conds)
                    .options(selectinload(ImsTransfer.lines).selectinload(ImsTransferLine.item))
                    .order_by(ImsTransfer.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        whs = await self._wh_map()
        names = await self._names({t.created_by for t in rows} - {None})
        return {
            "items": [await self._transfer_dict(t, whs, names) for t in rows],
            "total_items": total, "page": page, "page_size": page_size,
        }

    async def _return_dict(self, r: ImsReturn, whs: dict, names: dict, parties: dict) -> dict[str, Any]:
        return {
            "id": str(r.id),
            "reference_no": r.reference_no,
            "warehouse": self._wh_label(whs, r.warehouse_id),
            "party": parties.get(r.party_id),
            "return_date": r.return_date.isoformat(),
            "document_no": r.document_no,
            "notes": r.notes,
            "created_by": names.get(r.created_by),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "lines": [{"item": item_dict(l.item), "quantity": l.quantity} for l in r.lines],
            "total_quantity": sum(l.quantity for l in r.lines),
        }

    async def get_return(self, return_id: uuid.UUID) -> dict[str, Any]:
        r = (
            await self.session.execute(
                select(ImsReturn)
                .where(ImsReturn.id == return_id)
                .options(selectinload(ImsReturn.lines).selectinload(ImsReturnLine.item))
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if r is None:
            raise NotFoundError("Return not found.")
        parties = {uuid.UUID(p["id"]): p for p in await self.list_parties()}
        return await self._return_dict(
            r, await self._wh_map(), await self._names({r.created_by} - {None}), parties
        )

    async def list_returns(
        self, *, warehouse_id=None, party_id=None,  # noqa: ANN001
        date_from: date | None = None, date_to: date | None = None, page=1, page_size=25,
    ) -> dict[str, Any]:
        conds = []
        if warehouse_id:
            conds.append(ImsReturn.warehouse_id == warehouse_id)
        if party_id:
            conds.append(ImsReturn.party_id == party_id)
        if date_from:
            conds.append(ImsReturn.return_date >= date_from)
        if date_to:
            conds.append(ImsReturn.return_date <= date_to)
        total = (
            await self.session.execute(select(func.count()).select_from(ImsReturn).where(*conds))
        ).scalar_one()
        rows = (
            (
                await self.session.execute(
                    select(ImsReturn)
                    .where(*conds)
                    .options(selectinload(ImsReturn.lines).selectinload(ImsReturnLine.item))
                    .order_by(ImsReturn.return_date.desc(), ImsReturn.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        whs = await self._wh_map()
        names = await self._names({r.created_by for r in rows} - {None})
        parties = {uuid.UUID(p["id"]): p for p in await self.list_parties()}
        return {
            "items": [await self._return_dict(r, whs, names, parties) for r in rows],
            "total_items": total, "page": page, "page_size": page_size,
        }

    def _ledger_conditions(
        self, *, warehouse_id, item_id, movement_type, q, category, reference_no,  # noqa: ANN001
        date_from: date | None, date_to: date | None,
    ) -> list:
        conds = []
        if warehouse_id:
            conds.append(ImsLedger.warehouse_id == warehouse_id)
        if item_id:
            conds.append(ImsLedger.item_id == item_id)
        if movement_type:
            conds.append(ImsLedger.movement_type == movement_type)
        if reference_no:
            conds.append(ImsLedger.reference_no == reference_no)
        if q and q.strip():
            like = f"%{q.strip()}%"
            conds.append(
                ImsLedger.item_id.in_(
                    select(ImsItem.id).where(or_(ImsItem.name.ilike(like), ImsItem.sku.ilike(like)))
                )
            )
        if category:
            conds.append(ImsLedger.item_id.in_(select(ImsItem.id).where(ImsItem.category == category)))
        start, end = day_range_utc(date_from, date_to)
        if start:
            conds.append(ImsLedger.occurred_at >= start)
        if end:
            conds.append(ImsLedger.occurred_at < end)
        return conds

    async def _ledger_dicts(self, rows: list[ImsLedger]) -> list[dict[str, Any]]:
        whs = await self._wh_map()
        names = await self._names({r.actor_user_id for r in rows} - {None})
        return [
            {
                "id": str(r.id),
                "movement_type": r.movement_type,
                "warehouse": self._wh_label(whs, r.warehouse_id),
                "item": item_dict(r.item),
                "quantity_delta": r.quantity_delta,
                "quantity_before": r.quantity_before,
                "quantity_after": r.quantity_after,
                "reference_no": r.reference_no,
                "transfer_id": str(r.transfer_id) if r.transfer_id else None,
                "actor": names.get(r.actor_user_id),
                "occurred_at": r.occurred_at.isoformat(),
                "notes": r.notes,
            }
            for r in rows
        ]

    async def list_ledger(
        self, *, warehouse_id=None, item_id=None, movement_type=None, q=None,  # noqa: ANN001
        category=None, reference_no=None, date_from=None, date_to=None, page=1, page_size=25,
    ) -> dict[str, Any]:
        conds = self._ledger_conditions(
            warehouse_id=warehouse_id, item_id=item_id, movement_type=movement_type, q=q,
            category=category, reference_no=reference_no, date_from=date_from, date_to=date_to,
        )
        total = (
            await self.session.execute(select(func.count()).select_from(ImsLedger).where(*conds))
        ).scalar_one()
        rows = (
            (
                await self.session.execute(
                    select(ImsLedger)
                    .where(*conds)
                    .options(selectinload(ImsLedger.item))
                    .order_by(ImsLedger.occurred_at.desc(), ImsLedger.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        return {
            "items": await self._ledger_dicts(list(rows)),
            "total_items": total, "page": page, "page_size": page_size,
        }

    # ------------------------------------------------------------ historical
    async def stock_as_of(
        self, as_of: date | None = None, *, warehouse_id=None, kind=None,  # noqa: ANN001
        category=None, q=None,
    ) -> list[dict[str, Any]]:
        """Quantity per (warehouse, item) at the END of `as_of` (IST), summed
        from the ledger. `as_of=None` means 'now' (still ledger-derived)."""
        stmt = (
            select(ImsLedger.warehouse_id, ImsLedger.item_id, func.sum(ImsLedger.quantity_delta))
            .join(ImsItem, ImsItem.id == ImsLedger.item_id)
            .group_by(ImsLedger.warehouse_id, ImsLedger.item_id)
        )
        if as_of is not None:
            stmt = stmt.where(ImsLedger.occurred_at < ist_day_start_utc(as_of + timedelta(days=1)))
        if warehouse_id:
            stmt = stmt.where(ImsLedger.warehouse_id == warehouse_id)
        if kind:
            stmt = stmt.where(ImsItem.kind == kind)
        if category:
            stmt = stmt.where(ImsItem.category == category)
        if q and q.strip():
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(ImsItem.name.ilike(like), ImsItem.sku.ilike(like)))
        sums = (await self.session.execute(stmt)).all()
        items = {
            i.id: i
            for i in (
                await self.session.execute(
                    select(ImsItem).where(ImsItem.id.in_({r[1] for r in sums}))
                )
            ).scalars()
        } if sums else {}
        whs = await self._wh_map()
        out = []
        for wid, iid, qty in sums:
            if wid not in whs:
                continue
            out.append(
                {"warehouse": self._wh_label(whs, wid), "item": item_dict(items[iid]),
                 "quantity": int(qty or 0)}
            )
        out.sort(key=lambda r: (r["warehouse"]["code"] or "", r["item"]["name"]))
        return out

    # ------------------------------------------------------------- dashboard
    async def dashboard(
        self, *, warehouse_id=None, category=None, q=None,  # noqa: ANN001
        date_from: date | None = None, date_to: date | None = None,
    ) -> dict[str, Any]:
        whs = await self.list_warehouses()
        today = ist_today()
        date_to = date_to or today
        date_from = date_from or (date_to - timedelta(days=6))

        item_stmt = select(ImsItem).where(ImsItem.is_active.is_(True))
        if category:
            item_stmt = item_stmt.where(ImsItem.category == category)
        if q and q.strip():
            like = f"%{q.strip()}%"
            item_stmt = item_stmt.where(or_(ImsItem.name.ilike(like), ImsItem.sku.ilike(like)))
        items = {i.id: i for i in (await self.session.execute(item_stmt)).scalars()}
        per_wh = await self._qty_by_item_wh(list(items))

        def qty(item_id, wid) -> int:  # noqa: ANN001
            return per_wh.get((item_id, uuid.UUID(wid)), 0)

        scope = [w["id"] for w in whs if not warehouse_id or w["id"] == str(warehouse_id)]
        totals = {i: sum(qty(i, w) for w in scope) for i in items}
        status = {i: stock_status(totals[i], effective_threshold(items[i])) for i in items}

        finished = [i for i in items.values() if i.kind == KIND_FINISHED]
        packaging = [i for i in items.values() if i.kind == KIND_PACKAGING]
        pack_by_unit: dict[str, int] = defaultdict(int)
        for i in packaging:
            pack_by_unit[i.unit] += totals[i.id]

        def listing(kind_items, wanted: str) -> list[dict[str, Any]]:  # noqa: ANN001
            rows = [
                {**item_dict(i), "total": totals[i.id],
                 "by_warehouse": {w: qty(i.id, w) for w in scope}}
                for i in kind_items if status[i.id] == wanted
            ]
            return sorted(rows, key=lambda r: (r["total"], r["name"]))[:10]

        pool = list(items.values())
        low_stock = listing(pool, "low_stock")
        out_of_stock = listing(pool, "out_of_stock")

        # per-warehouse overview (not narrowed by the warehouse filter)
        start, end = day_range_utc(date_from, date_to)
        last_activity = {
            r[0]: r[1]
            for r in (
                await self.session.execute(
                    select(ImsLedger.warehouse_id, func.max(ImsLedger.occurred_at)).group_by(
                        ImsLedger.warehouse_id
                    )
                )
            ).all()
        }
        activity_counts = {
            r[0]: r[1]
            for r in (
                await self.session.execute(
                    select(ImsLedger.warehouse_id, func.count())
                    .where(ImsLedger.occurred_at >= start, ImsLedger.occurred_at < end)
                    .group_by(ImsLedger.warehouse_id)
                )
            ).all()
        }
        wh_cards = []
        for w in whs:
            wid = uuid.UUID(w["id"])
            held = [i for i in items.values() if qty(i.id, w["id"]) > 0]
            fin_held = [i for i in held if i.kind == KIND_FINISHED]
            low = [
                i for i in held
                if (t := effective_threshold(i)) is not None and qty(i.id, w["id"]) <= t
            ]
            wh_cards.append(
                {
                    **w,
                    "distinct_products": len(
                        {i.shopify_product_id or str(i.packaging_product_id) for i in held}
                    ),
                    "finished_skus": len(fin_held),
                    "finished_quantity": sum(qty(i.id, w["id"]) for i in fin_held),
                    "packaging_quantity_by_unit": {
                        u: sum(qty(i.id, w["id"]) for i in held if i.unit == u)
                        for u in PACKAGING_UNITS
                        if any(i.unit == u for i in held)
                    },
                    "low_stock_count": len(low),
                    "movements_in_range": activity_counts.get(wid, 0),
                    "last_activity": last_activity[wid].isoformat() if wid in last_activity else None,
                }
            )

        by_category: dict[str, int] = defaultdict(int)
        for i in finished:
            by_category[i.category or ims_catalog.UNCATEGORISED] += totals[i.id]

        move_conds = self._ledger_conditions(
            warehouse_id=warehouse_id, item_id=None, movement_type=None, q=q, category=category,
            reference_no=None, date_from=date_from, date_to=date_to,
        )
        recent_moves = (
            await self.session.execute(select(func.count()).select_from(ImsLedger).where(*move_conds))
        ).scalar_one()

        rt = await self.list_transfers(warehouse_id=warehouse_id, date_from=date_from, date_to=date_to, page_size=5)
        rr = await self.list_returns(warehouse_id=warehouse_id, date_from=date_from, date_to=date_to, page_size=5)
        ra = await self.list_adjustments(warehouse_id=warehouse_id, date_from=date_from, date_to=date_to, page_size=5)

        return {
            "filters": {
                "warehouse_id": str(warehouse_id) if warehouse_id else None,
                "category": category, "q": q,
                "date_from": str(date_from), "date_to": str(date_to),
            },
            "cards": {
                "total_physical_stock": sum(totals[i.id] for i in finished),
                "packaging_stock_by_unit": dict(pack_by_unit),
                "total_warehouses": len(whs),
                "active_warehouses": sum(1 for w in whs if w["is_active"]),
                "low_stock_items": sum(1 for s in status.values() if s == "low_stock"),
                "out_of_stock_items": sum(1 for s in status.values() if s == "out_of_stock"),
                "recent_stock_movements": recent_moves,
                "tracked_finished_items": len(finished),
                "tracked_packaging_items": len(packaging),
            },
            "warehouses": wh_cards,
            "stock_distribution": [
                {
                    "warehouse": w["name"], "code": w["code"],
                    "finished": sum(qty(i.id, w["id"]) for i in finished),
                    "packaging": sum(qty(i.id, w["id"]) for i in packaging),
                }
                for w in whs if not warehouse_id or w["id"] == str(warehouse_id)
            ],
            "category_stock": sorted(
                [{"category": c, "quantity": v} for c, v in by_category.items()],
                key=lambda r: -r["quantity"],
            ),
            "low_stock": low_stock,
            "out_of_stock": out_of_stock,
            "recent_transfers": rt["items"],
            "recent_returns": rr["items"],
            "recent_adjustments": ra["items"],
        }

    async def categories(self) -> list[str]:
        rows = await self.session.execute(
            select(ImsItem.category).where(ImsItem.category.is_not(None)).distinct()
        )
        return sorted(r[0] for r in rows.all())

