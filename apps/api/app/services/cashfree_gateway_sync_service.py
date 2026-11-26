"""Cashfree API-first synchronisation for Module 7 (prepaid reconciliation).

This is the orchestration layer ONLY -- it reuses, and does not duplicate,
the existing Cashfree integration:

  * `CashfreeClient` / `CashfreeConfig`      -- HTTP, auth, retry/backoff
  * `CashfreeSyncService.sync_transactions`  -- `POST /recon`        -> Payment
  * `CashfreeSyncService.sync_settlements`   -- `POST /settlements`  -> CashfreeSettlement
  * `PrepaidRemittanceReconciliationService` -- exact-id order matching
  * `remittance_common.auto_link_bank_credits` -- bank evidence by UTR

and adds the one missing piece: the transaction-level settlement rows
(`POST /settlement/recon`) stored as `GatewaySettlementLine` rows with
`source="api"`.

Financial-accuracy rules this module keeps:

  * Nothing is invented. A row without `event_settlement_amount` is skipped
    and counted; a missing UTR stays empty.
  * A Cashfree settlement that is SUCCESS is NOT proof of a bank credit.
    Bank evidence stays a separate step (`auto_link_bank_credits` links a
    bank credit only when it carries the same UTR; the dashboard keeps such
    a record "pending bank confirmation" until then).
  * In-flight events (pending lot / pending event) are stored but are never
    counted as settled; failed / reversed lots mark their lines failed.
  * Idempotent: each line is keyed by Cashfree's event id, so re-running any
    window updates in place and never duplicates. Manual matches are kept.
  * One sync at a time (a DB lock with a TTL); a crashed run's lock expires.
  * No credentials are ever logged, stored or returned.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.integrations.cashfree.client import CashfreeClient
from app.integrations.cashfree.config import CashfreeConfig
from app.integrations.cashfree.errors import CashfreeApiError
from app.integrations.cashfree.normalizer import (
    extract_settlement_rows,
    extract_settlement_webhook,
    normalize_settlement_recon_row,
)
from app.models.auth import User
from app.models.cashfree_settlement import CashfreeSettlement
from app.models.remittance_reconciliation import GatewaySettlementLine, GatewaySyncState
from app.services.cashfree_sync_service import CashfreeSyncService
from app.services.prepaid_remittance_recon_service import (
    MODULE,
    PrepaidRemittanceReconciliationService,
)
from app.services.remittance_common import (
    audit,
    auto_link_bank_credits,
    chunks,
    verification_of,
)

logger = get_logger(__name__)

GATEWAY = "cashfree"
GATEWAY_LABEL = "Cashfree"
MAX_PAGES_PER_RUN = 50
LOCK_TTL = timedelta(minutes=15)
# A manual sync waiting for a Celery worker. The Celery message expires after
# the same time, so an expired marker really means "this will never run".
QUEUE_TTL = timedelta(minutes=15)
QUEUED_PREFIX = "queued:"
QUEUE_EXPIRED_MESSAGE = (
    "The queued sync never finished: no Celery worker completed it. Check that the "
    "worker is running and uses the same broker as the API."
)
ERROR_MAX = 500

PENDING_LOT_STATES = {"PENDING", "PENDING_WITH_CASHFREE", "PENDING_WITH_BANK", "INITIATED"}
LOT_FAILED_STATES = {"FAILED", "REVERSED"}
# A webhook/API status never moves a lot "backwards" (e.g. a late
# SETTLEMENT_INITIATED must not undo SETTLEMENT_SUCCESS).
_LOT_RANK = {"PENDING": 0, "PENDING_WITH_CASHFREE": 0, "PENDING_WITH_BANK": 0, "INITIATED": 0}


def _lot_rank(status: str | None) -> int:
    value = (status or "").upper()
    if value in _LOT_RANK:
        return 0
    if value == "REVERSED":
        return 2
    return 1


def derive_line_status(
    event_status: str | None, lot_status: str | None, has_utr: bool
) -> str | None:
    """The status a synced line is stored with. Only a completed event in a
    completed (or unknown-but-UTR-bearing) settlement is allowed to count as
    settled; everything in flight is "pending", a failed/reversed lot fails
    its lines."""
    lot = (lot_status or "").upper()
    event = (event_status or "").strip().lower() or None
    if lot in LOT_FAILED_STATES:
        return lot.lower()
    if lot in PENDING_LOT_STATES:
        return "pending"
    if not lot and not has_utr:
        return "pending"
    # An unfamiliar settlement status is stored as-is, which keeps the line
    # unverified even when the event itself says success.
    if lot and verification_of(lot) != "successful":
        return lot.lower()[:32]
    return event


def _event_status(raw_row: Any) -> str | None:
    """The gateway event's own status as Cashfree sent it (nested or flat)."""
    if not isinstance(raw_row, dict):
        return None
    nested = raw_row.get("event_details")
    value = (nested.get("event_status") if isinstance(nested, dict) else None) or raw_row.get(
        "event_status"
    )
    return str(value) if value else None


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _line_key(row: dict[str, Any]) -> str:
    if row.get("event_id"):
        basis = f"cashfree|settlement_recon|{row['event_id']}"
    else:
        basis = "|".join(
            str(row.get(k))
            for k in (
                "cf_settlement_id",
                "event_type",
                "order_ref",
                "gateway_transaction_id",
                "net_amount",
                "transaction_date",
            )
        )
        basis = f"cashfree|settlement_recon|{basis}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def queued_at(last_status: str | None) -> datetime | None:
    if not last_status or not last_status.startswith(QUEUED_PREFIX):
        return None
    try:
        return datetime.fromtimestamp(int(last_status[len(QUEUED_PREFIX) :]), UTC)
    except ValueError:
        return None


def _safe_error(message: str) -> str:
    """Short, credential-free text for the status row / UI."""
    text = " ".join(str(message).split())
    for secret in (settings.CASHFREE_CLIENT_SECRET, settings.CASHFREE_CLIENT_ID):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:ERROR_MAX]


_COMPARE_FIELDS = (
    "gateway_transaction_id",
    "order_ref",
    "cf_settlement_id",
    "settlement_reference",
    "gross_amount",
    "gateway_charges",
    "gst_amount",
    "refund_amount",
    "net_amount",
    "transaction_date",
    "settlement_date",
    "line_status",
    "event_type",
)


class CashfreeGatewaySyncService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- State ---------------------------------------------------------------

    async def _get_state(self, *, create: bool) -> GatewaySyncState | None:
        state = (
            await self.session.execute(
                select(GatewaySyncState).where(GatewaySyncState.gateway == GATEWAY)
            )
        ).scalar_one_or_none()
        if state is None and create:
            state = GatewaySyncState(gateway=GATEWAY)
            self.session.add(state)
            try:
                await self.session.flush()
            except IntegrityError:
                await self.session.rollback()
                state = (
                    await self.session.execute(
                        select(GatewaySyncState).where(GatewaySyncState.gateway == GATEWAY)
                    )
                ).scalar_one()
        return state

    async def _acquire_lock(self) -> bool:
        state = await self._get_state(create=True)
        assert state is not None
        await self.session.commit()
        now = _now()
        result = await self.session.execute(
            update(GatewaySyncState)
            .where(
                GatewaySyncState.gateway == GATEWAY,
                (GatewaySyncState.lock_until.is_(None)) | (GatewaySyncState.lock_until < now),
            )
            .values(lock_until=now + LOCK_TTL)
        )
        await self.session.commit()
        return bool(result.rowcount)

    async def _release_lock(self) -> None:
        await self.session.execute(
            update(GatewaySyncState)
            .where(GatewaySyncState.gateway == GATEWAY)
            .values(lock_until=None)
        )
        await self.session.commit()

    # --- Queue (manual sync waiting for a worker) ------------------------------

    async def claim_queue_slot(self) -> tuple[str, str | None]:
        """Atomically record "a manual sync is queued", so repeated clicks queue
        it once. Returns ("queued", marker), or ("already_running" |
        "already_queued", None). The marker lives in `last_status`; the worker's
        result replaces it.
        """
        state = await self._get_state(create=True)
        assert state is not None
        await self.session.commit()
        await self.session.refresh(state)
        now = _now()
        if state.lock_until and _aware(state.lock_until) > now:
            return "already_running", None
        previous = state.last_status
        waiting_since = queued_at(previous)
        if waiting_since and now - waiting_since < QUEUE_TTL:
            return "already_queued", None
        marker = f"{QUEUED_PREFIX}{int(now.timestamp())}"
        result = await self.session.execute(
            update(GatewaySyncState)
            .where(
                GatewaySyncState.gateway == GATEWAY,
                (GatewaySyncState.lock_until.is_(None)) | (GatewaySyncState.lock_until < now),
                GatewaySyncState.last_status.is_(None)
                if previous is None
                else GatewaySyncState.last_status == previous,
            )
            .values(last_status=marker, last_error=None)
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()
        await self.session.refresh(state)
        if not result.rowcount:
            return "already_queued", None  # someone else claimed it first
        return "queued", marker

    async def abandon_queue_slot(self, marker: str, reason: str) -> None:
        """The task could not be queued: record it, unless a run has since
        replaced the marker."""
        await self.session.execute(
            update(GatewaySyncState)
            .where(GatewaySyncState.gateway == GATEWAY, GatewaySyncState.last_status == marker)
            .values(last_status="failed", last_error=_safe_error(reason))
            .execution_options(synchronize_session=False)
        )
        await self.session.commit()

    # --- Status (read-only, no Cashfree call) --------------------------------

    async def status(self) -> dict[str, Any]:
        config = CashfreeConfig.from_settings()
        state = await self._get_state(create=False)
        lots = (await self.session.execute(select(CashfreeSettlement))).scalars().all()
        by_status: dict[str, int] = {}
        for lot in lots:
            key = (lot.status or "UNKNOWN").upper()
            by_status[key] = by_status.get(key, 0) + 1
        api_lines = (
            await self.session.execute(
                select(GatewaySettlementLine.id).where(GatewaySettlementLine.source == "api")
            )
        ).all()

        running = bool(state and state.lock_until and _aware(state.lock_until) > _now())
        last_status = state.last_status if state else None
        last_error = state.last_error if state else None
        waiting_since = queued_at(last_status)
        queued = False
        if waiting_since is not None:
            if running:
                last_status = "running"
            elif _now() - waiting_since < QUEUE_TTL:
                queued, last_status = True, "queued"
            else:
                last_status, last_error = "failed", QUEUE_EXPIRED_MESSAGE
        connection, reason = self._connection(config, state, last_status, last_error)
        return {
            "gateway": GATEWAY_LABEL,
            "connection_status": connection,
            "connection_message": reason,
            "configured": config is not None,
            "environment": config.environment if config else None,
            "sync_enabled": bool(settings.CASHFREE_SYNC_ENABLED),
            "sync_interval_minutes": settings.CASHFREE_SYNC_INTERVAL_MINUTES,
            "last_attempt_at": state.last_attempt_at if state else None,
            "last_successful_sync_at": state.last_success_at if state else None,
            "last_sync_status": last_status,
            "last_sync_error": last_error,
            "queued": queued,
            "queued_at": waiting_since if queued else None,
            "last_sync_trigger": state.last_trigger if state else None,
            "synced_through": state.synced_through if state else None,
            "last_webhook_at": state.last_webhook_at if state else None,
            "last_result": state.last_result if state else None,
            "running": running,
            "settlement_lots": len(lots),
            "settlement_lots_by_status": by_status,
            "synced_lines": len(api_lines),
            "bank_confirmation_note": (
                "A Cashfree settlement marked SUCCESS is not proof the money reached "
                "the bank. Bank reconciliation is separate: a settlement is only "
                "bank-confirmed once a bank credit with the same UTR is linked."
            ),
        }

    @staticmethod
    def _connection(
        config: CashfreeConfig | None,
        state: GatewaySyncState | None,
        last: str | None,
        error: str | None,
    ) -> tuple[str, str]:
        if config is None:
            return (
                "not_configured",
                "Cashfree credentials are not configured on the server. "
                "Upload a settlement file as a fallback.",
            )
        if last in ("queued", "running"):
            return (
                "connected" if state and state.last_success_at else "never_synced",
                "A sync is queued for the background worker."
                if last == "queued"
                else "A sync is running.",
            )
        if error == QUEUE_EXPIRED_MESSAGE:
            return "error", error
        if last == "auth_error":
            return (
                "not_configured",
                "Cashfree rejected the credentials, or the API key does not have "
                "settlement/reconciliation permission.",
            )
        if state is None or (state.last_attempt_at is None and last != "failed"):
            return "never_synced", "Credentials are present; no sync has run yet."
        if last == "ok":
            return "connected", "Last sync completed successfully."
        if last == "partial":
            return "degraded", error or "The last sync completed only partially."
        if last == "already_running":
            return "connected", "A sync was already running."
        return "error", error or "The last sync failed."

    # --- Sync ------------------------------------------------------------------

    async def sync(
        self,
        *,
        trigger: str = "manual",
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        user: User | None = None,
    ) -> dict[str, Any]:
        started = _now()
        # Kept as an id: a rollback after a failed step expires ORM objects.
        user_id = user.id if user is not None else None
        config = CashfreeConfig.from_settings()
        if config is None:
            state = await self._get_state(create=True)
            assert state is not None
            state.last_attempt_at = started
            state.last_status = "not_configured"
            state.last_trigger = trigger
            state.last_error = None
            await self.session.commit()
            return {
                "status": "not_configured",
                "message": "Cashfree credentials are not configured; nothing was synced.",
                "trigger": trigger,
            }
        if not await self._acquire_lock():
            return {
                "status": "already_running",
                "message": "A Cashfree sync is already running. Try again shortly.",
                "trigger": trigger,
            }

        state = await self._get_state(create=True)
        assert state is not None
        window_to = date_to or started
        if date_from is not None:
            window_from = date_from
        elif state.synced_through is not None:
            window_from = _aware(state.synced_through) - timedelta(
                days=settings.CASHFREE_SYNC_LOOKBACK_DAYS
            )
        else:
            window_from = window_to - timedelta(days=settings.CASHFREE_SYNC_INITIAL_DAYS)
        if window_from > window_to:
            window_from = window_to - timedelta(days=1)

        steps: dict[str, Any] = {}
        errors: list[str] = []
        auth_error = False
        ok_steps = 0
        total_steps = 3
        try:
            legacy = CashfreeSyncService(self.session)
            for name, runner in (
                ("transactions", legacy.sync_transactions),
                ("settlements", legacy.sync_settlements),
            ):
                try:
                    res = await runner(window_from, window_to)
                    steps[name] = res.model_dump()
                    ok_steps += 1
                except IntegrationError as exc:
                    await self.session.rollback()
                    kind = (exc.details or {}).get("error_type")
                    auth_error = auth_error or kind == "authentication_error"
                    errors.append(f"{name}: {_safe_error(exc.message)}")
                    steps[name] = {"error": _safe_error(exc.message)}
            try:
                steps["settlement_lines"] = await self._sync_lines(
                    config, window_from, window_to, user_id
                )
                ok_steps += 1
            except CashfreeApiError as exc:
                await self.session.rollback()
                auth_error = auth_error or exc.error_type == "authentication_error"
                errors.append(f"settlement_lines: {_safe_error(exc.message)}")
                steps["settlement_lines"] = {"error": _safe_error(exc.message)}
        except Exception as exc:  # noqa: BLE001 - a scheduled run must never crash the worker
            await self.session.rollback()
            errors.append(_safe_error(f"unexpected: {exc}"))
            logger.error("cashfree_gateway_sync_unexpected", error=_safe_error(str(exc)))

        if auth_error:
            status = "auth_error"
        elif ok_steps == total_steps and not errors:
            status = "ok"
        elif ok_steps:
            status = "partial"
        else:
            status = "failed"

        finished = _now()
        state = await self._get_state(create=True)
        assert state is not None
        state.last_attempt_at = started
        state.last_trigger = trigger
        state.last_status = status
        state.last_error = _safe_error("; ".join(errors)) if errors else None
        state.last_result = {
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "window_from": window_from.isoformat(),
            "window_to": window_to.isoformat(),
            "steps": steps,
        }
        if status == "ok":
            state.last_success_at = finished
            # Only a fully successful run advances the watermark, so a partial
            # failure is simply retried over the same window next time.
            state.synced_through = window_to
        state.lock_until = None
        audit_user = await self.session.get(User, user_id) if user_id else None
        await audit(
            self.session,
            audit_user,
            "remittance.prepaid.cashfree_sync",
            "gateway_sync_state",
            state.id,
            new={"status": status, "trigger": trigger},
        )
        await self.session.commit()
        logger.info("cashfree_gateway_sync_finished", status=status, trigger=trigger)
        return {
            "status": status,
            "trigger": trigger,
            "window_from": window_from,
            "window_to": window_to,
            "steps": steps,
            "errors": errors,
            "started_at": started,
            "finished_at": finished,
        }

    async def release_after_crash(self, error_name: str) -> None:
        """Last resort after an unexpected failure: free the lock and record
        it, so a crashed run never leaves the sync stuck as "running"."""
        state = await self._get_state(create=True)
        assert state is not None
        state.last_status = "failed"
        state.last_error = _safe_error(f"unexpected failure: {error_name}")
        state.lock_until = None
        await self.session.commit()

    async def _lot_status_map(self) -> dict[str, str | None]:
        rows = (
            await self.session.execute(
                select(CashfreeSettlement.cf_settlement_id, CashfreeSettlement.status)
            )
        ).all()
        return {str(i): s for i, s in rows}

    async def _sync_lines(
        self,
        config: CashfreeConfig,
        window_from: datetime,
        window_to: datetime,
        user_id: Any,
    ) -> dict[str, Any]:
        client = CashfreeClient(config)
        counts = {
            "fetched": 0,
            "new": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped_incomplete": 0,
            "auto_matched": 0,
            "bank_links": 0,
            "pages": 0,
        }
        start = window_from.strftime("%Y-%m-%dT%H:%M:%SZ")
        end = window_to.strftime("%Y-%m-%dT%H:%M:%SZ")
        lots = await self._lot_status_map()
        matcher = PrepaidRemittanceReconciliationService(self.session)
        cursor: str | None = None
        now = _now()
        try:
            for _ in range(MAX_PAGES_PER_RUN):
                response = await client.get_settlement_reconciliation(
                    start_date=start, end_date=end, cursor=cursor
                )
                counts["pages"] += 1
                rows, cursor = extract_settlement_rows(response)
                counts["fetched"] += len(rows)

                parsed: dict[str, dict[str, Any]] = {}
                for raw in rows:
                    line = normalize_settlement_recon_row(raw)
                    if line is None:
                        counts["skipped_incomplete"] += 1
                        continue
                    parsed[_line_key(line)] = line

                existing: dict[str, GatewaySettlementLine] = {}
                for part in chunks(sorted(parsed)):
                    for found in (
                        (
                            await self.session.execute(
                                select(GatewaySettlementLine).where(
                                    GatewaySettlementLine.dedupe_key.in_(list(part))
                                )
                            )
                        )
                        .scalars()
                        .all()
                    ):
                        existing[found.dedupe_key] = found

                added: list[GatewaySettlementLine] = []
                for key, line in parsed.items():
                    fields = {k: v for k, v in line.items() if k != "event_id"}
                    fields["line_status"] = derive_line_status(
                        line.get("line_status"),
                        lots.get(line.get("cf_settlement_id") or ""),
                        bool(line.get("settlement_reference")),
                    )
                    current = existing.get(key)
                    if current is None:
                        obj = GatewaySettlementLine(
                            gateway=GATEWAY_LABEL,
                            dedupe_key=key,
                            source="api",
                            synced_at=now,
                            **fields,
                        )
                        self.session.add(obj)
                        added.append(obj)
                        counts["new"] += 1
                        continue
                    changed = any(getattr(current, k) != fields.get(k) for k in _COMPARE_FIELDS)
                    # Never clobber a real value with "missing" on a re-sync.
                    if changed:
                        for k in _COMPARE_FIELDS:
                            new_value = fields.get(k)
                            if new_value is None and getattr(current, k) is not None:
                                continue
                            setattr(current, k, new_value)
                        current.raw_row = fields.get("raw_row")
                        counts["updated"] += 1
                    else:
                        counts["unchanged"] += 1
                    current.synced_at = now
                    if current.matched_order_id is None:
                        added.append(current)
                await self.session.flush()
                if added:
                    counts["auto_matched"] += await matcher.match_lines(added)
                await self.session.commit()
                if cursor is None or not rows:
                    break
            else:
                logger.warning("cashfree_settlement_lines_page_cap_reached")
        finally:
            await client.aclose()

        refs = (
            await self.session.execute(
                select(GatewaySettlementLine.settlement_reference)
                .where(
                    GatewaySettlementLine.source == "api",
                    GatewaySettlementLine.settlement_reference.is_not(None),
                )
                .distinct()
            )
        ).scalars()
        link_user = await self.session.get(User, user_id) if user_id else None
        counts["bank_links"] = await auto_link_bank_credits(
            self.session, MODULE, list(refs), user=link_user
        )
        await self.session.commit()
        return counts

    # --- Webhook ---------------------------------------------------------------

    async def apply_settlement_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply a (signature-verified) settlement webhook. Updates the
        settlement lot's status/UTR (never backwards), re-derives its lines'
        status and links bank evidence by UTR. Idempotent."""
        event_type = str(payload.get("type") or "")
        parsed = extract_settlement_webhook(payload)
        if parsed is None:
            return {"applied": False, "reason": "no_settlement_id"}

        repo_fields = {k: v for k, v in parsed.items() if v is not None}
        settlement_id = repo_fields.pop("cf_settlement_id")
        status = repo_fields.get("status")
        if not status:
            status = {
                "SETTLEMENT_INITIATED": "PENDING",
                "SETTLEMENT_SUCCESS": "SUCCESS",
                "SETTLEMENT_FAILED": "FAILED",
                "SETTLEMENT_REVERSED": "REVERSED",
            }.get(event_type)
            if status:
                repo_fields["status"] = status
        status = str(status).upper() if status else None
        if status:
            repo_fields["status"] = status

        lot = (
            await self.session.execute(
                select(CashfreeSettlement).where(
                    CashfreeSettlement.cf_settlement_id == settlement_id
                )
            )
        ).scalar_one_or_none()
        if lot is not None and status and _lot_rank(status) < _lot_rank(lot.status):
            repo_fields.pop("status", None)  # stale event: keep the later status
            status = (lot.status or "").upper() or None
        if lot is None:
            lot = CashfreeSettlement(
                cf_settlement_id=settlement_id,
                raw_external_payload=payload,
                **repo_fields,
            )
            self.session.add(lot)
        else:
            for key, value in repo_fields.items():
                setattr(lot, key, value)
        await self.session.flush()

        lines = (
            (
                await self.session.execute(
                    select(GatewaySettlementLine).where(
                        GatewaySettlementLine.cf_settlement_id == settlement_id,
                        GatewaySettlementLine.source == "api",
                    )
                )
            )
            .scalars()
            .all()
        )
        utr = lot.settlement_utr
        for line in lines:
            if not line.settlement_reference and utr:
                line.settlement_reference = utr
            line.line_status = derive_line_status(
                _event_status(line.raw_row), status, bool(line.settlement_reference)
            )
        linked = 0
        if utr:
            linked = await auto_link_bank_credits(self.session, MODULE, [utr], user=None)

        state = await self._get_state(create=True)
        assert state is not None
        state.last_webhook_at = _now()
        await self.session.commit()
        return {
            "applied": True,
            "cf_settlement_id": settlement_id,
            "status": status,
            "lines_updated": len(lines),
            "bank_links": linked,
        }
