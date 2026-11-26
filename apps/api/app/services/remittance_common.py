"""Shared building blocks for Modules 6 (COD) and 7 (Prepaid) remittance
reconciliation: tolerance, status rules, percentage objects, bank-credit
evidence, original-file storage and audit logging.

Principles (financial accuracy over attractive numbers):

- Nothing is *assumed* settled. A delivered COD order with no remittance
  line is `pending`; a paid prepaid order with no settlement line is
  `pending`. Only evidence rows (lines / bank credits / links) move a record
  forward.
- `matched` needs BANK evidence too: the remittance / settlement reference
  must be covered by linked bank credits. Lines that agree with the expected
  amount but have no bank credit are `partially_reconciled`.
- "Settled" is what the courier / gateway REPORTS; "bank confirmed" is what
  linked bank credits prove. They are separate figures (see `DEFINITIONS`)
  and are never added together.
- A partial amount is `short`, never `matched`.
- A partial bank credit counts exactly what was credited. When one reference
  covers several lines, the credit is split pro rata (to the paisa) and no
  order under that reference is `matched` until the whole reference is covered.
- One rupee of a bank credit can be used once: links and manual verifications
  never add up to more than the credit (see `BankLedger`).
- Only an explicitly recognised success status counts as a settlement. Blank
  or unfamiliar statuses are "unverified".
- Differences within `TOLERANCE` (rounding) count as equal.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models.auth import User
from app.models.cod_remittance import CodRemittance
from app.models.remittance_reconciliation import (
    BankCredit,
    BankCreditLink,
    CodRemittanceLine,
    GatewaySettlementLine,
    ReconciliationAdjustment,
    SettlementFile,
)
from app.models.shipment import Shipment
from app.services.audit_service import AuditService
from app.services.remittance_upload_parser import sha256_hex, validate_file

ZERO = Decimal("0.00")
TOLERANCE = Decimal("1.00")
IST = timedelta(hours=5, minutes=30)

# --- gateway settlement statuses ---------------------------------------------
# ALLOWLIST. Only these count as a successful settlement. Compared after
# `normalise_status` (case, spaces and hyphens do not matter). Documented in
# docs/integrations/cashfree.md; change both together.
SUCCESS_LINE_STATES = frozenset({"success", "successful", "settled", "processed", "completed"})
# Recognised as NOT successful: failed / undone, or still in flight.
FAILED_LINE_STATES = frozenset(
    {
        "failed",
        "failure",
        "reversed",
        "cancelled",
        "declined",
        "rejected",
        "void",
        "error",
        "user_dropped",
        "not_attempted",
        "incomplete",
        "flagged",
        "pending",
        "initiated",
        "processing",
        "in_progress",
        "on_hold",
    }
)
# Everything else (blank, unknown, unfamiliar) is "unverified": never counted.
VERIFICATION_LABELS = {
    "successful": "Successful",
    "unsuccessful": "Not successful",
    "unverified": "Unverified",
}


def normalise_status(value: str | None) -> str:
    return "_".join((value or "").strip().lower().replace("-", " ").split())


def verification_of(value: str | None) -> str:
    state = normalise_status(value)
    if state in SUCCESS_LINE_STATES:
        return "successful"
    if state in FAILED_LINE_STATES:
        return "unsuccessful"
    return "unverified"


def is_successful(value: str | None) -> bool:
    return verification_of(value) == "successful"


BANK_STATUS_LABELS = {
    "not_applicable": "Nothing expected",
    "not_confirmed": "Not Confirmed",
    "partially_confirmed": "Partially Confirmed",
    "confirmed": "Confirmed",
}
EVIDENCE_LABELS = {
    "statement_matched": "Statement-matched",
    "manually_verified": "Manually verified",
}


STATUS_LABELS = {
    "pending": "Pending",
    "partially_reconciled": "Partially Reconciled",
    "matched": "Matched",
    "short": "Short",
    "excess": "Excess",
    "unmatched": "Unmatched",
    "failed": "Failed",
    "refunded": "Refunded",
}

# One wording per figure, written into every Excel export. The web app has the
# same wording in `apps/web/lib/remittance-definitions.ts`.
_SHARED_DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "Total expected",
        "What the bank should receive for the orders in view (COD: COD amount; prepaid: "
        "expected net of paid orders).",
    ),
    (
        "Remaining amount",
        "Total expected - amount bank confirmed, per order and never below zero. Money "
        "confirmed above an order's expected amount is shown separately as over-confirmed.",
    ),
    (
        "Partially Confirmed",
        "Some, but not all, of the expected amount is bank confirmed. The actual credited "
        "amount counts. When one reference covers several orders the credit is split pro "
        "rata, and none of them is Matched until the whole reference is covered.",
    ),
    (
        "Statement-matched",
        "Bank statement credit whose reference equals the report reference / UTR.",
    ),
    (
        "Manually verified",
        "Recorded by an ADMIN with a reason, bank reference and amount: a manual link of a "
        "statement credit, or a Shiprocket remittance verification. Audit logged.",
    ),
    (
        "Manual adjustments",
        "Reasoned per-order adjustments. They change the per-order status difference, never "
        "bank-confirmed money.",
    ),
    (
        "Unmatched Bank Credits",
        "Bank credit amounts not allocated to any remittance or settlement reference "
        "(COD or prepaid). Never added to reported amounts.",
    ),
)

DEFINITIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "cod": (
        (
            "Total COD Settled",
            "Remittance amount reported by the courier partner for delivered COD orders "
            "in view. Not proof of a bank credit.",
        ),
        (
            "COD Bank Confirmed",
            "Courier-reported remittance covered by actual bank credits (statement-matched "
            "or manually verified). Never more than was credited.",
        ),
        (
            "Pending Settlement",
            "COD amount of delivered orders with no courier-reported remittance and no adjustment.",
        ),
        (
            "Difference / Short / Excess",
            "COD settled (courier-reported) + manual adjustments - COD amount. Bank credits "
            "do not change it.",
        ),
        (
            "Unmatched Remittances",
            "Courier-reported remittance lines not matched to a delivered COD order.",
        ),
        *_SHARED_DEFINITIONS,
    ),
    "prepaid": (
        (
            "Total Settlement Received",
            "Successful net settlement reported by Cashfree and by other gateways' uploaded "
            "reports, for prepaid orders in view. Not proof of a bank credit.",
        ),
        (
            "Successful settlement statuses",
            "Only these count (case, spaces and hyphens ignored): "
            + ", ".join(sorted(SUCCESS_LINE_STATES))
            + ".",
        ),
        (
            "Unverified",
            "A settlement line whose status is blank, unknown or unfamiliar. It is listed "
            "but counts toward no settlement total.",
        ),
        (
            "Prepaid Bank Confirmed",
            "Successful gateway-reported net settlement covered by actual bank credits "
            "(statement-matched or manually verified). Never more than was credited.",
        ),
        (
            "Settlement Pending",
            "Gross amount captured on orders with no successful gateway-reported "
            "settlement and no adjustment.",
        ),
        (
            "Settlement Difference",
            "Expected net (gross - charges - GST - refunds) - Total Settlement Received. "
            "It includes amounts still pending; bank credits and manual adjustments do "
            "not change it.",
        ),
        (
            "Status difference (per order)",
            "Settlement received + manual adjustments - expected net. Decides Short / Excess.",
        ),
        (
            "Unmatched Transactions",
            "Gateway-reported settlement lines not matched to a prepaid order. Lines that "
            "are not successful or are unverified are listed but add nothing to the amount.",
        ),
        *_SHARED_DEFINITIONS,
    ),
}


def add_bank_summary_sheet(
    workbook: Any, kpis: dict[str, Any], extra: Sequence[tuple[str, Any]] = ()
) -> None:
    position = kpis["bank_position"]
    sheet = workbook.create_sheet("Bank & adjustments")
    rows: list[tuple[str, Any]] = [
        ("Total expected", position["expected"]),
        ("Amount bank confirmed", position["amount"]),
        ("of which statement-matched", position["statement_matched"]),
        ("of which manually verified", position["manually_verified"]),
        ("Remaining amount", position["remaining"]),
        ("Over-confirmed above expected", position["over_confirmed"]),
        ("Partially Confirmed orders", kpis["partially_confirmed"]["count"]),
        ("Remaining on Partially Confirmed orders", kpis["partially_confirmed"]["amount"]),
        ("Orders with manual adjustments", kpis["adjusted"]["count"]),
        ("Manual adjustments", kpis["adjusted"]["amount"]),
        *extra,
    ]
    for label, value in rows:
        sheet.append([label, float(value)])
    sheet.column_dimensions["A"].width = 52
    sheet.column_dimensions["B"].width = 18


# (header, record key) appended to every order export.
BANK_EXPORT_COLUMNS = (
    ("Total expected (bank)", "bank_expected"),
    ("Statement-matched", "bank_statement_amount"),
    ("Manually verified", "bank_manual_amount"),
    ("Remaining amount", "bank_remaining"),
    ("Over-confirmed", "bank_over"),
    ("Bank confirmation status", "bank_status_label"),
    ("Bank evidence type", "bank_evidence"),
    ("Manual adjustments (amount)", "adjustment_amount"),
    ("Status difference before adjustments", "difference_before_adjustments"),
    ("Adjustment effect", "adjustment_effect"),
)


def export_value(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


def add_definitions_sheet(workbook: Any, module: str) -> None:
    sheet = workbook.create_sheet("Definitions")
    sheet.append(["Figure", "Definition"])
    for term, meaning in DEFINITIONS[module]:
        sheet.append([term, meaning])
    sheet.column_dimensions["A"].width = 32
    sheet.column_dimensions["B"].width = 120


AGEING_BUCKETS = (
    ("0-7 days", 0, 7),
    ("8-15 days", 8, 15),
    ("16-30 days", 16, 30),
    ("31-60 days", 31, 60),
    ("60+ days", 61, 10**6),
)


def dec(value: Decimal | float | int | None) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def percent(numerator: Decimal | int, denominator: Decimal | int) -> float | None:
    """Share as a percentage, `None` (never 0/NaN/Infinity) when the
    denominator is zero.
    """
    if not denominator:
        return None
    return float(Decimal(str(numerator)) / Decimal(str(denominator)) * 100)


def percentage_item(
    key: str,
    label: str,
    numerator: Decimal | int,
    denominator: Decimal | int,
    *,
    kind: str,
    numerator_label: str,
    denominator_label: str,
    drill: str | None = None,
) -> dict[str, Any]:
    """One displayed percentage with its numerator, denominator and what
    each means (so the UI can always show the denominator, and tell a
    transaction-count share from a money-value share).
    """
    return {
        "key": key,
        "label": label,
        "kind": kind,  # "count" | "value"
        "numerator": numerator,
        "denominator": denominator,
        "numerator_label": numerator_label,
        "denominator_label": denominator_label,
        "value": percent(numerator, denominator),
        "drill": drill,
    }


def cod_status(
    expected: Decimal,
    remitted: Decimal,
    adjustments: Decimal,
    bank_confirmed: Decimal,
    fully_covered: bool = True,
) -> str:
    """Status of one delivered COD order from its evidence."""
    settled = remitted + adjustments
    if remitted == 0 and adjustments == 0:
        return "pending"
    diff = settled - expected
    if diff < -TOLERANCE:
        return "short"
    if diff > TOLERANCE:
        return "excess"
    if remitted == 0:  # fully covered by a reasoned manual adjustment
        return "matched"
    # The tolerance covers rounding only: with no bank credit at all, even an
    # amount smaller than the tolerance is not bank-confirmed.
    # `fully_covered` is False while any reference behind the order is only
    # partly credited: a pro-rata share is never enough to call it matched.
    if fully_covered and bank_confirmed > 0 and bank_confirmed >= remitted - TOLERANCE:
        return "matched"
    return "partially_reconciled"


def ageing_bucket(days: int) -> str:
    for label, low, high in AGEING_BUCKETS:
        if low <= days <= high:
            return label
    return AGEING_BUCKETS[-1][0]


def to_ist_date(value: datetime) -> str:
    return (value.astimezone(UTC) + IST).date().isoformat()


def norm_ref(value: str | None) -> str | None:
    return value.strip().lower() if value and value.strip() else None


def chunks(items: Sequence[Any], size: int = 400) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


@dataclass
class BankEvidence:
    """Applied bank money per (lower-cased) reference, for one module.
    `credited` is the total; `manual` is the part of it that rests on a manual
    link (the rest is statement-matched).
    """

    credited: dict[str, Decimal]
    credit_dates: dict[str, datetime]
    manual: dict[str, Decimal] = field(default_factory=dict)

    def covers(self, reference: str | None, batch_total: Decimal) -> bool:
        """True only when linked bank credits exist for the reference AND they
        cover its whole batch (within rounding)."""
        ref = norm_ref(reference)
        if not ref:
            return False
        credited = self.credited.get(ref, ZERO)
        return credited > 0 and credited >= batch_total - TOLERANCE


def prorate(weights: Sequence[Decimal], target: Decimal) -> list[Decimal]:
    """Split `target` over `weights` in whole paise (largest remainder, ties by
    position). The shares always add up to exactly `target`."""
    total = sum(weights, ZERO)
    if not weights or total == 0 or target == 0:
        return [ZERO for _ in weights]
    paise = int(money(target) * 100)
    exact = [Decimal(paise) * w / total for w in weights]
    floors = [int(x.to_integral_value(rounding=ROUND_FLOOR)) for x in exact]
    left = paise - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: (floors[i] - exact[i], i))
    for i in order[:left]:
        floors[i] += 1
    return [money(Decimal(x) / 100) for x in floors]


@dataclass
class LineBank:
    """Bank money applied to one report line."""

    confirmed: Decimal = ZERO
    manual: Decimal = ZERO
    covered: bool = False  # its whole reference is covered
    reference: str | None = None


def allocate_to_lines(
    lines: Sequence[tuple[Any, str | None, Decimal]], evidence: BankEvidence
) -> dict[Any, LineBank]:
    """`lines` = (id, reference, amount) of EVERY counted line of the
    references involved, in a stable order. Each reference applies
    min(credited, reported total): the actual credited amount when partial,
    never more than was reported. A reference reported at zero or less gets
    nothing."""
    out: dict[Any, LineBank] = {}
    groups: dict[str, list[tuple[Any, Decimal]]] = defaultdict(list)
    for line_id, reference, amount in lines:
        ref = norm_ref(reference)
        out[line_id] = LineBank(reference=ref)
        if ref:
            groups[ref].append((line_id, amount))
    for ref, members in groups.items():
        total = sum((amount for _id, amount in members), ZERO)
        credited = evidence.credited.get(ref, ZERO)
        if total <= 0 or credited <= 0:
            continue
        applied = min(credited, total)
        statement = credited - evidence.manual.get(ref, ZERO)
        shares = prorate([amount for _id, amount in members], applied)
        # Statement money is used first; only the remainder rests on manual links.
        by_statement = prorate(shares, min(max(statement, ZERO), applied))
        covered = evidence.covers(ref, total)
        for (line_id, _amount), share, from_statement in zip(
            members, shares, by_statement, strict=True
        ):
            out[line_id] = LineBank(share, share - from_statement, covered, ref)
    return out


async def reference_lines(
    session: AsyncSession, module: str, references: Iterable[str | None]
) -> list[tuple[Any, str, Decimal]]:
    """Every counted report line carrying one of these references."""
    wanted = sorted({norm_ref(r) for r in references if norm_ref(r)})
    if module == "cod":
        model, ref_col = CodRemittanceLine, CodRemittanceLine.remittance_reference
        amount_col, date_col = (
            CodRemittanceLine.remittance_amount,
            CodRemittanceLine.remittance_date,
        )
        status_col = None
    else:
        model, ref_col = GatewaySettlementLine, GatewaySettlementLine.settlement_reference
        amount_col, date_col = (
            GatewaySettlementLine.net_amount,
            GatewaySettlementLine.settlement_date,
        )
        status_col = GatewaySettlementLine.line_status
    out: list[tuple[Any, str, Decimal]] = []
    for part in chunks(wanted):
        columns = [model.id, ref_col, amount_col]
        if status_col is not None:
            columns.append(status_col)
        rows = await session.execute(
            select(*columns)
            .where(func.lower(func.trim(ref_col)).in_(list(part)))
            .order_by(date_col, model.created_at, model.id)
        )
        for row in rows.all():
            if status_col is not None and not is_successful(row[3]):
                continue
            out.append((row[0], row[1], dec(row[2])))
    return out


async def reference_totals(
    session: AsyncSession, module: str, references: Iterable[str | None]
) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for _id, reference, amount in await reference_lines(session, module, references):
        totals[norm_ref(reference)] += amount
    return dict(totals)


@dataclass
class BankLedger:
    """How every bank credit is used, across BOTH modules.

    Invariant: for each statement credit,
    links + manual verifications against it <= the credited amount.
    """

    credits: list[BankCredit]
    links: list[tuple[BankCreditLink, Decimal]]  # (link, amount that applies)
    linked: dict[uuid.UUID, Decimal]  # per credit, all modules
    claimed: dict[uuid.UUID, Decimal]  # per credit, Shiprocket manual verifications
    claims: dict[str, Decimal]  # per AWB: the manually verified amount that applies

    def free(self, credit: BankCredit) -> Decimal:
        used = self.linked.get(credit.id, ZERO) + self.claimed.get(credit.id, ZERO)
        return max(dec(credit.amount) - used, ZERO)


async def _verification_claims(session: AsyncSession) -> list[CodRemittance]:
    """Manual verifications that are in effect: the AWB's order has no uploaded
    remittance line (an uploaded line replaces the Shiprocket figure)."""
    rows = (
        (
            await session.execute(
                select(CodRemittance)
                .where(
                    CodRemittance.bank_verified_at.is_not(None),
                    CodRemittance.bank_credit_amount.is_not(None),
                )
                .order_by(CodRemittance.bank_verified_at, CodRemittance.awb)
            )
        )
        .scalars()
        .all()
    )
    replaced: set[str] = set()
    for part in chunks([r.awb for r in rows]):
        replaced.update(
            (
                await session.execute(
                    select(Shipment.awb)
                    .join(
                        CodRemittanceLine,
                        CodRemittanceLine.matched_order_id == Shipment.order_id,
                    )
                    .where(Shipment.awb.in_(list(part)))
                )
            )
            .scalars()
            .all()
        )
    return [r for r in rows if r.awb not in replaced and dec(r.remitted_amount) > 0]


async def load_ledger(session: AsyncSession, *, exclude_awb: str | None = None) -> BankLedger:
    credits = list(
        (
            await session.execute(
                select(BankCredit).order_by(
                    BankCredit.credit_date, BankCredit.created_at, BankCredit.id
                )
            )
        )
        .scalars()
        .all()
    )
    active = list(
        (
            await session.execute(
                select(BankCreditLink)
                .where(BankCreditLink.reversed_at.is_(None))
                .order_by(BankCreditLink.created_at, BankCreditLink.id)
            )
        )
        .scalars()
        .all()
    )
    by_credit: dict[uuid.UUID, list[BankCreditLink]] = defaultdict(list)
    for link in active:
        by_credit[link.bank_credit_id].append(link)
    links: list[tuple[BankCreditLink, Decimal]] = []
    linked: dict[uuid.UUID, Decimal] = {}
    for credit in credits:
        mine = by_credit.get(credit.id, [])
        amounts = [dec(link.amount) for link in mine]
        total = sum(amounts, ZERO)
        # Writes prevent this; if stored links ever exceed the credit, scale them down.
        if total > dec(credit.amount):
            amounts = prorate(amounts, dec(credit.amount))
            total = dec(credit.amount)
        links.extend(zip(mine, amounts, strict=True))
        linked[credit.id] = total

    credits_by_ref: dict[str, list[BankCredit]] = defaultdict(list)
    for credit in credits:
        if norm_ref(credit.reference):
            credits_by_ref[norm_ref(credit.reference)].append(credit)
    claims_by_ref: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    claims: dict[str, Decimal] = {}
    for rem in await _verification_claims(session):
        if rem.awb == exclude_awb:
            continue
        amount = max(min(dec(rem.bank_credit_amount), dec(rem.remitted_amount)), ZERO)
        ref = norm_ref(rem.bank_credit_reference)
        if ref and ref in credits_by_ref:
            claims_by_ref[ref].append((rem.awb, amount))
        else:  # no statement credit to compare with: the manual figure stands
            claims[rem.awb] = amount
    claimed: dict[uuid.UUID, Decimal] = {}
    for ref, members in claims_by_ref.items():
        room = {
            c.id: max(dec(c.amount) - linked.get(c.id, ZERO), ZERO) for c in credits_by_ref[ref]
        }
        wanted = sum((amount for _awb, amount in members), ZERO)
        usable = min(wanted, sum(room.values(), ZERO))
        for (awb, _amount), share in zip(
            members, prorate([a for _w, a in members], usable), strict=True
        ):
            claims[awb] = share
        for credit_id, space in room.items():
            take = min(space, usable)
            claimed[credit_id] = take
            usable -= take
    return BankLedger(credits, links, linked, claimed, claims)


def evidence_from(ledger: BankLedger, module: str) -> BankEvidence:
    credit_by_id = {c.id: c for c in ledger.credits}
    credited: dict[str, Decimal] = {}
    manual: dict[str, Decimal] = {}
    dates: dict[str, datetime] = {}
    for link, amount in ledger.links:
        ref = norm_ref(link.reference)
        if link.module != module or not ref or amount <= 0:
            continue
        credited[ref] = credited.get(ref, ZERO) + amount
        if link.method != "auto":
            manual[ref] = manual.get(ref, ZERO) + amount
        when = credit_by_id[link.bank_credit_id].credit_date
        if when and (ref not in dates or when > dates[ref]):
            dates[ref] = when
    return BankEvidence(credited, dates, manual)


async def load_bank_evidence(session: AsyncSession, module: str) -> BankEvidence:
    return evidence_from(await load_ledger(session), module)


async def lock_bank_credits(session: AsyncSession, ids: Sequence[uuid.UUID]) -> None:
    """Row locks so two requests cannot allocate the same money at once
    (PostgreSQL; SQLite serialises writers itself)."""
    for part in chunks(sorted(ids, key=str)):
        await session.execute(
            select(BankCredit.id)
            .where(BankCredit.id.in_(list(part)))
            .order_by(BankCredit.id)  # one lock order, so two callers cannot deadlock
            .with_for_update()
        )


async def auto_link_bank_credits(
    session: AsyncSession, module: str, references: Iterable[str | None], *, user: User | None
) -> int:
    """Statement-match: link bank credits whose reference equals a known
    remittance / settlement reference, for no more than the reference still
    needs and no more than the credit still has free. Idempotent; a later run
    tops up an automatic link when the reference grows. Reversed and manual
    links are left alone.
    """
    wanted = {norm_ref(r) for r in references if norm_ref(r)}
    if not wanted:
        return 0
    found: list[tuple[uuid.UUID, str]] = []
    for part in chunks(sorted(wanted)):
        found.extend(
            (
                await session.execute(
                    select(BankCredit.id, BankCredit.reference)
                    .where(func.lower(func.trim(BankCredit.reference)).in_(list(part)))
                    .order_by(BankCredit.credit_date, BankCredit.created_at, BankCredit.id)
                )
            ).all()
        )
    if not found:
        return 0
    await lock_bank_credits(session, [credit_id for credit_id, _ref in found])
    ledger = await load_ledger(session)
    totals = await reference_totals(session, module, {ref for _id, ref in found})
    evidence = evidence_from(ledger, module)
    credit_by_id = {c.id: c for c in ledger.credits}
    free = {credit_id: ledger.free(credit_by_id[credit_id]) for credit_id, _ref in found}
    need = {ref: total - evidence.credited.get(ref, ZERO) for ref, total in totals.items()}
    changed = 0
    for credit_id, raw_reference in found:
        ref = norm_ref(raw_reference)
        amount = money(min(free[credit_id], need.get(ref, ZERO)))
        if amount <= 0:
            continue
        existing = await session.scalar(
            select(BankCreditLink).where(
                BankCreditLink.bank_credit_id == credit_id,
                BankCreditLink.module == module,
                func.lower(BankCreditLink.reference) == ref,
            )
        )
        if existing is None:
            session.add(
                BankCreditLink(
                    bank_credit_id=credit_id,
                    module=module,
                    reference=raw_reference.strip(),
                    amount=amount,
                    method="auto",
                    linked_by=user.id if user else None,
                )
            )
        elif existing.reversed_at is None and existing.method == "auto":
            existing.amount = dec(existing.amount) + amount
        else:
            continue
        free[credit_id] -= amount
        need[ref] -= amount
        changed += 1
    await session.flush()
    return changed


def bank_position(
    expected: Decimal, confirmed: Decimal, manual: Decimal, covered: bool
) -> dict[str, Any]:
    """Bank confirmation of one order against the total expected for it."""
    expected = max(expected, ZERO)
    remaining = max(expected - confirmed, ZERO)
    over = max(confirmed - expected, ZERO)
    if confirmed <= 0:
        status = "not_confirmed" if expected > 0 else "not_applicable"
    elif covered and confirmed >= expected - TOLERANCE:
        status = "confirmed"
    else:
        status = "partially_confirmed"
    statement = confirmed - manual
    kinds = [
        label
        for label, amount in (("statement_matched", statement), ("manually_verified", manual))
        if amount != 0
    ]
    return {
        "bank_expected": money(expected),
        "bank_statement_amount": money(statement),
        "bank_manual_amount": money(manual),
        "bank_remaining": money(remaining),
        "bank_over": money(over),
        "bank_status": status,
        "bank_status_label": BANK_STATUS_LABELS[status],
        "bank_evidence": " + ".join(EVIDENCE_LABELS[k] for k in kinds) or None,
    }


# Drilldown selectors shared by both modules.
BANK_SELECTORS = {
    "bank_remaining": lambda r: r["bank_remaining"] != 0,
    "partially_confirmed": lambda r: r["bank_status"] == "partially_confirmed",
    "adjusted": lambda r: r["adjustment_amount"] != 0,
}
BANK_MEASURES = {
    "bank_remaining": ("Remaining to be bank confirmed", lambda r: r["bank_remaining"]),
    "partially_confirmed": (
        "Remaining on Partially Confirmed orders",
        lambda r: r["bank_remaining"],
    ),
    "adjusted": ("Manual adjustments", lambda r: r["adjustment_amount"]),
}


def bank_and_adjustment_kpis(records: list[dict], confirmed_key: str) -> dict[str, Any]:
    def total(rows: list[dict], key: str) -> Decimal:
        return money(sum((r[key] for r in rows), ZERO))

    picked = {name: [r for r in records if test(r)] for name, test in BANK_SELECTORS.items()}
    partial = picked["partially_confirmed"]
    return {
        "bank_remaining": {
            "count": len(picked["bank_remaining"]),
            "amount": total(picked["bank_remaining"], "bank_remaining"),
        },
        "partially_confirmed": {
            "count": len(partial),
            "amount": total(partial, "bank_remaining"),
            "expected": total(partial, "bank_expected"),
            "confirmed": total(partial, confirmed_key),
        },
        "bank_position": {
            "count": len(records),
            "amount": total(records, confirmed_key),
            "expected": total(records, "bank_expected"),
            "statement_matched": total(records, "bank_statement_amount"),
            "manually_verified": total(records, "bank_manual_amount"),
            "remaining": total(records, "bank_remaining"),
            "over_confirmed": total(records, "bank_over"),
        },
        "adjusted": {
            "count": len(picked["adjusted"]),
            "amount": total(picked["adjusted"], "adjustment_amount"),
        },
    }


def _signed(value: Decimal) -> str:
    return f"{'-' if value < 0 else '+' if value > 0 else ''}Rs {abs(money(value)):,.2f}"


def adjustment_effect(
    *,
    before: Decimal,
    adjustment: Decimal,
    status_before: str,
    status_after: str,
    applies: bool = True,
) -> str:
    """Plain-language effect of manual adjustments on one order's status
    difference."""
    if adjustment == 0:
        return "No manual adjustment."
    if not applies:
        return (
            f"Manual adjustment {_signed(adjustment)} has no effect: no payment is captured, "
            "so no status difference is calculated."
        )
    after = before + adjustment
    change = (
        f"status stays {STATUS_LABELS[status_after]}"
        if status_before == status_after
        else f"status changes from {STATUS_LABELS[status_before]} to {STATUS_LABELS[status_after]}"
    )
    return (
        f"Manual adjustment {_signed(adjustment)} moves the status difference from "
        f"{_signed(before)} to {_signed(after)}; {change}. It is not bank money and does "
        "not change the settlement difference."
    )


async def store_upload(
    session: AsyncSession,
    *,
    module: str,
    source_name: str,
    filename: str,
    content_type: str | None,
    data: bytes,
    user: User | None,
) -> SettlementFile:
    """Persist the original bytes. The same bytes can never be stored twice
    for a module (re-uploading identical content is a conflict, not a
    second set of financial entries).
    """
    validate_file(filename, data)
    digest = sha256_hex(data)
    existing = await session.scalar(
        select(SettlementFile).where(
            SettlementFile.module == module, SettlementFile.sha256 == digest
        )
    )
    if existing is not None:
        raise ConflictError(
            f"This exact file was already uploaded on {existing.created_at:%d %b %Y} "
            f"as '{existing.filename}'. Nothing was imported again."
        )
    record = SettlementFile(
        module=module,
        source_name=source_name.strip()[:128],
        filename=filename[:255],
        content_type=content_type,
        size_bytes=len(data),
        sha256=digest,
        content=data,
        uploaded_by=user.id if user else None,
    )
    session.add(record)
    await session.flush()
    return record


async def audit(
    session: AsyncSession,
    user: User | None,
    action: str,
    entity_type: str,
    entity_id: Any,
    *,
    previous: dict | None = None,
    new: dict | None = None,
    metadata: dict | None = None,
) -> None:
    await AuditService(session).record(
        user=user,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        previous_value=previous,
        new_value=new,
        metadata=metadata,
    )


def paginate(items: list, page_params: Any) -> tuple[list, int]:
    start = page_params.offset
    return items[start : start + page_params.page_size], len(items)


def fmt_dt(value: datetime | None) -> str | None:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M") if value else None


def round_pct(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


# ------------------------------------------------------------- drilldowns


def drill_totals(
    measure: str,
    rows: list[dict],
    base_values: list[Decimal],
    *,
    kinds: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Totals over EVERY row of a drilldown (not just one page). `base_*` is
    the same selection without the search term, i.e. what the card shows."""
    out: dict[str, Any] = {
        "measure": measure,
        "count": len(rows),
        "amount": money(sum((r["contribution"] for r in rows), ZERO)),
        "base_count": len(base_values),
        "base_amount": money(sum(base_values, ZERO)),
        "breakdown": [],
    }
    for kind, label in (kinds or {}).items():
        part = [r for r in rows if r["kind"] == kind]
        out["breakdown"].append(
            {
                "label": label,
                "count": len(part),
                "amount": money(sum((r["contribution"] for r in part), ZERO)),
            }
        )
    return out


def neutralise_formulas(workbook: Any) -> None:
    """Spreadsheet-injection guard: text that came from customers / reports is
    always written as text, never as a formula."""
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.data_type = "s"


def drilldown_workbook(
    columns: list[tuple[str, Any]], rows: list[dict], totals: dict[str, Any], *, module: str
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Records"
    sheet.append([header for header, _ in columns])
    for row in rows:
        sheet.append([getter(row) for _, getter in columns])
    summary = workbook.create_sheet("Totals")
    summary.append(["Measure", totals["measure"]])
    summary.append(["Records exported", totals["count"]])
    summary.append(["Total of exported records", float(totals["amount"])])
    summary.append(["Records for the card (without search)", totals["base_count"]])
    summary.append(["Card total (without search)", float(totals["base_amount"])])
    for part in totals["breakdown"]:
        summary.append([f"{part['label']} ({part['count']})", float(part["amount"])])
    add_definitions_sheet(workbook, module)
    neutralise_formulas(workbook)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


async def bank_links_for(
    session: AsyncSession, module: str, references: Iterable[str | None]
) -> list[dict[str, Any]]:
    wanted = {norm_ref(r) for r in references if norm_ref(r)}
    if not wanted:
        return []
    found = (
        await session.execute(
            select(BankCreditLink, BankCredit)
            .join(BankCredit, BankCredit.id == BankCreditLink.bank_credit_id)
            .where(
                BankCreditLink.module == module,
                BankCreditLink.reversed_at.is_(None),
                func.lower(func.trim(BankCreditLink.reference)).in_(sorted(wanted)),
            )
            .order_by(BankCreditLink.created_at, BankCreditLink.id)
        )
    ).all()
    return [link_row(link, credit) for link, credit in found]


def link_row(link: BankCreditLink, credit: BankCredit) -> dict[str, Any]:
    manual = link.method != "auto"
    kind = "manually_verified" if manual else "statement_matched"
    return {
        "id": link.id,
        "module": link.module,
        "reference": link.reference,
        "linked_amount": money(dec(link.amount)),
        "method": link.method,
        "verification": kind,
        "verification_label": EVIDENCE_LABELS[kind],
        "bank_reference": link.bank_reference if manual else credit.reference,
        "reason": link.note if manual else None,
        "credit_amount": money(dec(credit.amount)),
        "credit_date": credit.credit_date,
        "narration": credit.narration,
    }


async def reference_positions(
    session: AsyncSession, module: str, references: Iterable[str | None]
) -> list[dict[str, Any]]:
    """Per reference: total reported under it, what the bank confirms, what is
    left."""
    names: dict[str, str] = {}
    for reference in references:
        if norm_ref(reference):
            names.setdefault(norm_ref(reference), reference.strip())
    if not names:
        return []
    totals = await reference_totals(session, module, names)
    evidence = await load_bank_evidence(session, module)
    out = []
    for ref, shown in names.items():
        total = totals.get(ref, ZERO)
        credited = evidence.credited.get(ref, ZERO)
        applied = max(min(credited, total), ZERO)
        manual = applied - min(max(credited - evidence.manual.get(ref, ZERO), ZERO), applied)
        position = bank_position(total, applied, manual, evidence.covers(ref, total))
        out.append({"reference": shown, **position})
    return out


async def adjustments_for(
    session: AsyncSession, module: str, order_id: Any
) -> list[dict[str, Any]]:
    found = (
        (
            await session.execute(
                select(ReconciliationAdjustment)
                .where(
                    ReconciliationAdjustment.module == module,
                    ReconciliationAdjustment.order_id == order_id,
                )
                .order_by(ReconciliationAdjustment.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "amount": money(dec(a.amount)),
            "reason": a.reason,
            "created_at": a.created_at,
            "reversed": a.reversed_at is not None,
        }
        for a in found
    ]
