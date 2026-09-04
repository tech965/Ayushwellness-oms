"""Cashfree -> OMS bulk synchronization.

Two independent, operator-triggered syncs (never auto-polled — spec:
no background auto-sync):

  - `sync_transactions` — `POST /recon`, applied through the EXISTING,
    unmodified `CashfreePaymentService.apply_payment_event` for every
    row. This module never writes to `Payment`/`PaymentTransaction`
    directly, so it inherits that method's exact idempotency/duplicate-
    safety/PAID-is-terminal/amount-validation rules for free — running
    the same date range twice can never create a duplicate `Payment` or
    downgrade an already-PAID one, because `apply_payment_event` already
    guarantees that for the webhook path this reuses verbatim.

  - `sync_settlements` — `POST /settlements`, upserted into the
    dedicated `CashfreeSettlement` table (never `Payment`) via
    `CashfreeSettlementRepository.upsert_by_cf_settlement_id`, keyed on
    Cashfree's own `cf_settlement_id`.

Both paginate via Cashfree's `cursor`/`pagination.limit` contract,
confirmed against Cashfree's current official API reference (not
guessed — see docs/integrations/cashfree.md).

On derived settlement figures: Cashfree's own MCP tooling exposes
dedicated `get_unsettled_amount`/`get_next_settlement_date` calls, which
confirms Cashfree tracks these as first-class concepts — but this
integration's research could not confirm a documented public REST
endpoint for either one. Rather than guess a plausible-looking path,
`get_settlement_summary` below DERIVES "unsettled amount" and "upcoming
settlement" from the confirmed `/settlements` list endpoint's own
`status`/`amount_settled` fields, and every derived field is documented
as such wherever it's returned — never presented as if Cashfree returned
it directly. Swap this for the real dedicated endpoint if/when its exact
contract is confirmed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.integrations.cashfree.client import CashfreeClient
from app.integrations.cashfree.config import CashfreeConfig
from app.integrations.cashfree.errors import CashfreeApiError
from app.integrations.cashfree.normalizer import (
    extract_recon_rows,
    extract_settlement_rows,
    normalize_recon_row,
    normalize_settlement_row,
)
from app.repositories.cashfree_settlement import CashfreeSettlementRepository
from app.schemas.cashfree import (
    CashfreeSettlementItem,
    CashfreeSettlementSummaryResponse,
    CashfreeSyncResult,
)
from app.services.cashfree_payment_service import CashfreePaymentService

logger = get_logger(__name__)

# One sync call fetches at most this many pages (each up to 1000 rows,
# Cashfree's documented max page size) before returning — a generous
# bound so a single request can't run forever against a huge date range;
# the operator simply re-runs sync (idempotent either way) for a range
# too large to finish in one call. Mirrors the bounded-crawl convention
# every other pull-sync in this codebase already uses.
_MAX_PAGES_PER_SYNC = 50


class CashfreeSyncService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.payment_service = CashfreePaymentService(session)
        self.settlements = CashfreeSettlementRepository(session)

    def _require_config(self) -> CashfreeConfig:
        config = CashfreeConfig.from_settings()
        if config is None:
            raise IntegrationError(
                "Cashfree integration is not configured "
                "(missing CASHFREE_CLIENT_ID/CASHFREE_CLIENT_SECRET).",
                details={"error_type": "not_configured"},
            )
        return config

    # --- Transaction sync (POST /recon) ---------------------------------

    async def sync_transactions(
        self, date_from: datetime, date_to: datetime
    ) -> CashfreeSyncResult:
        config = self._require_config()
        client = CashfreeClient(config)
        result = CashfreeSyncResult()
        start = date_from.strftime("%Y-%m-%dT%H:%M:%SZ")
        end = date_to.strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor: str | None = None

        try:
            for _ in range(_MAX_PAGES_PER_SYNC):
                try:
                    response = await client.get_reconciliation(
                        start_date=start, end_date=end, cursor=cursor
                    )
                except CashfreeApiError as exc:
                    logger.error(
                        "cashfree_sync_transactions_page_failed",
                        error_type=exc.error_type,
                        status_code=exc.status_code,
                    )
                    raise IntegrationError(
                        exc.message, details={"error_type": exc.error_type}
                    ) from exc

                rows, cursor = extract_recon_rows(response)
                result.fetched += len(rows)

                for raw in rows:
                    result.processed += 1
                    try:
                        kwargs = normalize_recon_row(raw)
                        if kwargs is None:
                            result.skipped += 1
                            continue
                        event_result = await self.payment_service.apply_payment_event(**kwargs)
                    except Exception as exc:  # noqa: BLE001 - one bad row must not kill the sync
                        result.failures += 1
                        result.errors.append(str(exc))
                        logger.error(
                            "cashfree_sync_transaction_row_failed",
                            cashfree_order_id=raw.get("order_id"),
                            error=str(exc),
                        )
                        continue

                    if event_result.applied:
                        result.applied += 1
                    elif event_result.reason == "duplicate_transaction":
                        result.duplicates += 1
                    else:
                        result.skipped += 1

                if cursor is None or not rows:
                    break

            logger.info(
                "cashfree_sync_transactions_completed",
                fetched=result.fetched,
                processed=result.processed,
                applied=result.applied,
                duplicates=result.duplicates,
                skipped=result.skipped,
                failures=result.failures,
            )
            return result
        finally:
            await client.aclose()

    # --- Settlement sync (POST /settlements) -----------------------------

    async def sync_settlements(
        self, date_from: datetime, date_to: datetime
    ) -> CashfreeSyncResult:
        config = self._require_config()
        client = CashfreeClient(config)
        result = CashfreeSyncResult()
        start = date_from.strftime("%Y-%m-%d")
        end = date_to.strftime("%Y-%m-%d")
        cursor: str | None = None

        try:
            for _ in range(_MAX_PAGES_PER_SYNC):
                try:
                    response = await client.get_settlements(
                        start_date=start, end_date=end, cursor=cursor
                    )
                except CashfreeApiError as exc:
                    logger.error(
                        "cashfree_sync_settlements_page_failed",
                        error_type=exc.error_type,
                        status_code=exc.status_code,
                    )
                    raise IntegrationError(
                        exc.message, details={"error_type": exc.error_type}
                    ) from exc

                rows, cursor = extract_settlement_rows(response)
                result.fetched += len(rows)

                for raw in rows:
                    result.processed += 1
                    try:
                        kwargs = normalize_settlement_row(raw)
                        if kwargs is None:
                            result.skipped += 1
                            continue
                        _, created = await self.settlements.upsert_by_cf_settlement_id(**kwargs)
                        await self.session.commit()
                    except Exception as exc:  # noqa: BLE001 - one bad row must not kill the sync
                        await self.session.rollback()
                        result.failures += 1
                        result.errors.append(str(exc))
                        logger.error(
                            "cashfree_sync_settlement_row_failed",
                            cf_settlement_id=raw.get("cf_settlement_id"),
                            error=str(exc),
                        )
                        continue

                    if created:
                        result.applied += 1
                    else:
                        result.duplicates += 1

                if cursor is None or not rows:
                    break

            logger.info(
                "cashfree_sync_settlements_completed",
                fetched=result.fetched,
                processed=result.processed,
                applied=result.applied,
                duplicates=result.duplicates,
                skipped=result.skipped,
                failures=result.failures,
            )
            return result
        finally:
            await client.aclose()

    # --- Settlement analytics (local DB, no live Cashfree call) ---------

    async def get_settlement_summary(self, *, limit: int = 20) -> CashfreeSettlementSummaryResponse:
        """Reads only the locally-synced `cashfree_settlements` table —
        run `sync_settlements` first (or the caller sees whatever was
        last synced, honestly, never a live call disguised as instant).
        """
        last_settled = await self.settlements.get_last_settled()
        upcoming = await self.settlements.get_nearest_upcoming()
        unsettled_total = await self.settlements.get_unsettled_total()
        recent = await self.settlements.list_recent(limit=limit)

        return CashfreeSettlementSummaryResponse(
            # DERIVED (see this module's docstring) -- sum of every
            # settlement not yet in a terminal SUCCESS/FAILED state.
            unsettled_amount=unsettled_total,
            # DERIVED -- the nearest not-yet-completed settlement's own
            # gross amount, never Cashfree's own dedicated field.
            upcoming_settlement_amount=upcoming.payment_amount if upcoming else None,
            upcoming_settlement_status=upcoming.status if upcoming else None,
            last_settled_amount=last_settled.amount_settled if last_settled else None,
            last_settled_date=last_settled.settlement_processed_on if last_settled else None,
            last_settlement_utr=last_settled.settlement_utr if last_settled else None,
            last_settlement_status=last_settled.status if last_settled else None,
            history=[
                CashfreeSettlementItem(
                    cf_settlement_id=row.cf_settlement_id,
                    status=row.status,
                    settlement_utr=row.settlement_utr,
                    settlement_processed_on=row.settlement_processed_on,
                    payment_amount=row.payment_amount,
                    amount_settled=row.amount_settled,
                )
                for row in recent
            ],
        )
