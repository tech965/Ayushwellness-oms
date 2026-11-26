"""Bank statements and the manual evidence tools shared by Modules 6 & 7:
bank-credit import, bank-credit links, reasoned-adjustment reversal and
access to the preserved original files.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.auth import User
from app.models.remittance_reconciliation import (
    BankCredit,
    BankCreditLink,
    CodRemittanceLine,
    GatewaySettlementLine,
    ReconciliationAdjustment,
    SettlementFile,
)
from app.services import remittance_upload_parser as parser
from app.services.remittance_common import (
    ZERO,
    audit,
    auto_link_bank_credits,
    dec,
    evidence_from,
    load_ledger,
    lock_bank_credits,
    money,
    norm_ref,
    reference_totals,
    store_upload,
)

MODULES = ("cod", "prepaid")


async def _known_references(session: AsyncSession, module: str) -> list[str | None]:
    if module == "cod":
        col = CodRemittanceLine.remittance_reference
    else:
        col = GatewaySettlementLine.settlement_reference
    return list((await session.execute(select(col).distinct())).scalars().all())


class BankStatementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def import_statement(
        self,
        *,
        source_name: str,
        filename: str,
        content_type: str | None,
        data: bytes,
        user: User | None,
        column_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        parsed = parser.parse_bank_credits(filename, data, column_map)
        record = await store_upload(
            self.session,
            module="bank",
            source_name=(source_name or "Bank statement").strip(),
            filename=filename,
            content_type=content_type,
            data=data,
            user=user,
        )
        keys = [r["dedupe_key"] for r in parsed.rows]
        existing = (
            set(
                (
                    await self.session.execute(
                        select(BankCredit.dedupe_key).where(BankCredit.dedupe_key.in_(keys))
                    )
                )
                .scalars()
                .all()
            )
            if keys
            else set()
        )
        added = 0
        duplicates = parsed.skipped
        for row in parsed.rows:
            if row["dedupe_key"] in existing:
                duplicates += 1
                continue
            self.session.add(BankCredit(file_id=record.id, **row))
            added += 1
        await self.session.flush()
        linked = {}
        credit_refs = {norm_ref(r["reference"]) for r in parsed.rows if r["reference"]}
        for module in MODULES:
            known = {norm_ref(r) for r in await _known_references(self.session, module)}
            linked[module] = await auto_link_bank_credits(
                self.session, module, credit_refs & known, user=user
            )
        record.row_count = parsed.total_rows
        record.accepted_count = added
        record.duplicate_count = duplicates
        record.rejected_count = len(parsed.errors)
        record.row_errors = parsed.errors[:100]
        await audit(
            self.session,
            user,
            "remittance.bank.upload",
            "settlement_file",
            record.id,
            new={"file": filename, "credits": added},
        )
        await self.session.commit()
        return {
            "file_id": record.id,
            "filename": filename,
            "rows_read": parsed.total_rows,
            "accepted": added,
            "duplicates_or_non_credits_skipped": duplicates,
            "rejected": len(parsed.errors),
            "auto_linked_cod": linked["cod"],
            "auto_linked_prepaid": linked["prepaid"],
            "row_errors": parsed.errors[:50],
            "column_mapping": parsed.column_mapping,
            "ignored_columns": parsed.ignored_columns,
        }

    async def link_bank_credit(
        self,
        *,
        module: str,
        bank_credit_id: uuid.UUID,
        reference: str,
        bank_reference: str,
        amount: Decimal,
        reason: str,
        user: User,
    ) -> BankCreditLink:
        """ADMIN manual verification: "this statement credit pays that report
        reference". Reason, bank reference and amount are mandatory; the amount
        can exceed neither what is free on the credit nor what the reference
        still needs.
        """
        ref = reference.strip()
        bank_ref = bank_reference.strip()
        why = reason.strip()
        if not ref:
            raise ValidationError("A remittance / settlement reference is required.")
        if not bank_ref:
            raise ValidationError("The bank reference / UTR you verified is required.")
        if len(why) < 3:
            raise ValidationError("A reason is required for a manual bank verification.")
        apply = money(amount)
        if apply <= 0:
            raise ValidationError("The amount must be greater than zero.")
        await lock_bank_credits(self.session, [bank_credit_id])
        credit = await self.session.get(BankCredit, bank_credit_id)
        if credit is None:
            raise NotFoundError("Bank credit not found.")
        known = {norm_ref(r) for r in await _known_references(self.session, module)}
        if ref.lower() not in known:
            raise ValidationError(f"No uploaded {module} report has the reference '{ref}'.")
        existing = await self.session.scalar(
            select(BankCreditLink).where(
                BankCreditLink.bank_credit_id == credit.id,
                BankCreditLink.module == module,
                func.lower(BankCreditLink.reference) == ref.lower(),
            )
        )
        if existing is not None and existing.reversed_at is None:
            raise ValidationError("This bank credit is already linked to that reference.")
        ledger = await load_ledger(self.session)
        free = money(ledger.free(credit))
        if apply > free:
            raise ValidationError(f"Only {free} of this bank credit is still unallocated.")
        totals = await reference_totals(self.session, module, [ref])
        confirmed = evidence_from(ledger, module).credited.get(ref.lower(), ZERO)
        need = money(max(totals.get(ref.lower(), ZERO) - confirmed, ZERO))
        if apply > need:
            raise ValidationError(
                f"Reference '{ref}' only needs {need} more. Money cannot be allocated twice "
                "or beyond what was reported."
            )
        previous = None
        if existing is not None:
            previous = {"amount": str(existing.amount), "method": existing.method, "reversed": True}
            existing.reversed_at = None
            existing.reversed_by = None
            existing.amount = apply
            existing.method = "manual"
            existing.linked_by = user.id
            existing.note = why[:255]
            existing.bank_reference = bank_ref[:128]
            link = existing
        else:
            link = BankCreditLink(
                bank_credit_id=credit.id,
                module=module,
                reference=ref,
                amount=apply,
                method="manual",
                linked_by=user.id,
                note=why[:255],
                bank_reference=bank_ref[:128],
            )
            self.session.add(link)
        await self.session.flush()
        await audit(
            self.session,
            user,
            f"remittance.{module}.bank_link",
            "bank_credit_link",
            link.id,
            previous=previous,
            new={
                "verification": "manually_verified",
                "bank_credit_id": str(credit.id),
                "credit_amount": str(money(dec(credit.amount))),
                "credit_reference": credit.reference,
                "reference": ref,
                "bank_reference": bank_ref,
                "amount": str(apply),
                "reason": why,
            },
        )
        await self.session.commit()
        return link

    async def reverse_bank_link(self, module: str, link_id: uuid.UUID, user: User) -> None:
        link = await self.session.get(BankCreditLink, link_id)
        if link is None or link.module != module:
            raise NotFoundError("Bank link not found.")
        if link.reversed_at is not None:
            raise ValidationError("This link was already reversed.")
        link.reversed_at = datetime.now(UTC)
        link.reversed_by = user.id
        await audit(
            self.session,
            user,
            f"remittance.{module}.bank_unlink",
            "bank_credit_link",
            link.id,
            previous={"reference": link.reference, "amount": str(link.amount)},
        )
        await self.session.commit()

    async def reverse_adjustment(self, module: str, adjustment_id: uuid.UUID, user: User) -> None:
        adj = await self.session.get(ReconciliationAdjustment, adjustment_id)
        if adj is None or adj.module != module:
            raise NotFoundError("Adjustment not found.")
        if adj.reversed_at is not None:
            raise ValidationError("This adjustment was already reversed.")
        adj.reversed_at = datetime.now(UTC)
        adj.reversed_by = user.id
        await audit(
            self.session,
            user,
            f"remittance.{module}.adjustment_reversed",
            "reconciliation_adjustment",
            adj.id,
            previous={"amount": str(adj.amount), "reason": adj.reason},
        )
        await self.session.commit()

    async def list_uploads(self, module: str) -> list[SettlementFile]:
        return list(
            (
                await self.session.execute(
                    select(SettlementFile)
                    .where(SettlementFile.module.in_((module, "bank")))
                    .order_by(SettlementFile.created_at.desc())
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )

    async def get_file(self, file_id: uuid.UUID) -> SettlementFile:
        record = await self.session.get(SettlementFile, file_id)
        if record is None:
            raise NotFoundError("File not found.")
        return record
