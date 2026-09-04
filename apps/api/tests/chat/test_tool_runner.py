"""ToolRunner: each tool call must return real OMS numbers, be gated on
the caller's permissions, and fail safely."""

from __future__ import annotations

import json

import pytest
from app.chat.tool_runner import ToolRunner
from app.repositories.auth import UserRepository
from sqlalchemy.ext.asyncio import AsyncSession

from tests.chat.conftest import NOW, make_order
from tests.conftest import _create_user_with_permissions

pytestmark = pytest.mark.asyncio


def _payload(outcome):
    assert outcome.ok, outcome.content
    return json.loads(outcome.content)


async def test_operations_summary_today_counts_and_split(
    db_session: AsyncSession, chat_user, seeded_orders
) -> None:
    runner = ToolRunner(db_session, chat_user, now=NOW)
    out = await runner.run("get_operations_summary", {"period": "today"})
    body = _payload(out)

    assert body["orders"]["value"] == "3"
    assert body["orders"]["raw"] == 3
    assert body["cod_orders"]["value"] == "2"
    assert body["prepaid_orders"]["value"] == "1"
    # revenue includes the cancelled order (existing OMS rule): 1000+500+2000
    assert body["revenue"]["value"] == "₹3,500"
    assert body["cod_share"] == "66.7%"
    # compared with yesterday (2 prepaid orders): +50%
    assert body["orders"]["previous"] == "2"
    assert body["orders"]["change"] == "+50.0%"
    assert "OMS database" in body["sources"][0]


async def test_operations_summary_without_compare(
    db_session: AsyncSession, chat_user, seeded_orders
) -> None:
    runner = ToolRunner(db_session, chat_user, now=NOW)
    body = _payload(
        await runner.run(
            "get_operations_summary", {"period": "today", "compare_to_previous": False}
        )
    )
    assert "previous" not in body["orders"]


async def test_orders_breakdown_groups_by_status(
    db_session: AsyncSession, chat_user, seeded_orders
) -> None:
    runner = ToolRunner(db_session, chat_user, now=NOW)
    body = _payload(await runner.run("get_orders_breakdown", {"period": "today"}))
    assert body["by_order_status"].get("cancelled") == 1
    assert body["by_payment_type"].get("cod") == 2
    assert body["by_payment_type"].get("prepaid") == 1


async def test_list_orders_returns_sample_and_total(
    db_session: AsyncSession, chat_user, seeded_orders
) -> None:
    runner = ToolRunner(db_session, chat_user, now=NOW)
    body = _payload(await runner.run("list_orders", {"period": "today", "status": "cancelled"}))
    assert body["total_matching"] == 1
    assert body["orders"][0]["order_number"] == "T-2"
    assert body["orders"][0]["order_status"] == "cancelled"
    assert "IST" in body["orders"][0]["placed_at"]


async def test_top_products_ranks_by_units(
    db_session: AsyncSession, chat_user, seeded_orders
) -> None:
    # add a second product with more units today
    today = NOW.replace(hour=7)
    await make_order(
        db_session,
        chat_user,
        number="T-9",
        when=today,
        product_name="Triphala 120ct",
        sku="AW-TRI-120",
        quantity=5,
        unit_price="300.00",
    )
    runner = ToolRunner(db_session, chat_user, now=NOW)
    body = _payload(await runner.run("get_top_products", {"period": "today", "limit": 3}))
    assert body["products"][0]["title"] == "Triphala 120ct"
    assert body["products"][0]["units_sold"] == 5
    assert body["products"][0]["rank"] == 1


async def test_compare_periods_today_vs_yesterday(
    db_session: AsyncSession, chat_user, seeded_orders
) -> None:
    runner = ToolRunner(db_session, chat_user, now=NOW)
    body = _payload(
        await runner.run(
            "compare_periods",
            {"period_a": {"period": "yesterday"}, "period_b": {"period": "today"}},
        )
    )
    assert body["metrics"]["orders"]["period_a"] == "2"
    assert body["metrics"]["orders"]["period_b"] == "3"
    assert body["metrics"]["orders"]["change_a_to_b"] == "+50.0%"


async def test_compare_periods_needs_both_ranges(db_session: AsyncSession, chat_user) -> None:
    runner = ToolRunner(db_session, chat_user, now=NOW)
    out = await runner.run("compare_periods", {"period_a": {"period": "today"}, "period_b": {}})
    assert not out.ok
    assert out.error_code == "bad_date_range"


async def test_data_freshness_reports_no_syncs_when_empty(
    db_session: AsyncSession, chat_user
) -> None:
    runner = ToolRunner(db_session, chat_user, now=NOW)
    body = _payload(await runner.run("get_data_freshness", {}))
    assert body["syncs"] == []


async def test_bad_date_preset_is_a_safe_failure(db_session: AsyncSession, chat_user) -> None:
    runner = ToolRunner(db_session, chat_user, now=NOW)
    out = await runner.run("get_operations_summary", {"period": "since_holi"})
    assert not out.ok
    assert out.error_code == "bad_date_range"
    assert "since_holi" in json.loads(out.content)["message"]


async def test_unknown_tool_is_rejected(db_session: AsyncSession, chat_user) -> None:
    runner = ToolRunner(db_session, chat_user, now=NOW)
    out = await runner.run("drop_table_orders", {})
    assert not out.ok
    assert out.error_code == "unknown_tool"


async def test_rbac_restricts_tools_to_the_callers_permissions(
    db_session: AsyncSession, seeded_orders
) -> None:
    # A support-style user: orders.read but NOT analytics.read. Re-fetch
    # with permissions eager-loaded, exactly as the auth dependency does.
    created = await _create_user_with_permissions(
        db_session, email="support@example.com", permission_codes=["orders.read"]
    )
    support = await UserRepository(db_session).get_with_permissions(created.id)
    runner = ToolRunner(db_session, support, now=NOW)

    denied = await runner.run("get_operations_summary", {"period": "today"})
    assert not denied.ok
    assert denied.error_code == "not_authorized"

    allowed = await runner.run("list_orders", {"period": "today"})
    assert allowed.ok
    assert _payload(allowed)["total_matching"] == 3
