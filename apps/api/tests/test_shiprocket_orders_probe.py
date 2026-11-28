"""The orders probe reports field presence and vocabularies without leaking values."""

from __future__ import annotations

import pytest
from scripts.shiprocket_orders_probe import probe, summarize_order_details

pytestmark = pytest.mark.asyncio


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    async def request(self, method, path, params=None):
        assert method == "GET"  # read-only, always
        self.calls.append((path, params))
        if path == "/orders":
            return {
                "data": [
                    {"id": 1, "payment_method": "cod", "status_code": "7"},
                    {"id": 2, "payment_method": "cod", "status_code": "7"},
                ],
                "meta": {"pagination": {"total": 40, "total_pages": 20, "per_page": 2}},
            }
        return {
            "data": {
                "id": 1 if path.endswith("/1") else 2,
                "remittance_utr": "SECRET-UTR-1" if path.endswith("/1") else "",
                "remittance_date": "2026-11-20" if path.endswith("/1") else "",
                "remittance_status": "Paid" if path.endswith("/1") else "",
                "cod_recieved_hover_text": "COD received" if path.endswith("/1") else None,
                "customer_name": "Private Person",
            }
        }


async def test_probe_uses_only_get_and_documented_cod_filter_and_prints_no_values():
    client = _Client()
    text = "\n".join(await probe(client, sample=5, pause=0))
    assert client.calls[0] == (
        "/orders",
        {"filter_by": "payment_method", "filter": "cod", "per_page": 5, "page": 1},
    )
    assert [p for p, _ in client.calls[1:]] == ["/orders/show/1", "/orders/show/2"]
    assert "total COD orders reported=40" in text
    assert "data.remittance_utr: present in 2/2, non-empty in 1" in text
    assert "value 'Paid' x1" in text and "value 'COD received' x1" in text  # vocabularies
    # Never the UTR, a date, or customer data.
    for secret in ("SECRET-UTR-1", "2026-11-20", "Private Person", "customer_name"):
        assert secret not in text


async def test_probe_reports_missing_fields_and_empty_pages():
    class Empty:
        async def request(self, method, path, params=None):
            return {"data": [], "meta": {"pagination": {"total": 0, "total_pages": 0}}}

    text = "\n".join(await probe(Empty(), sample=3, pause=0))
    assert "orders returned=0" in text and "order-detail responses read: 0" in text
    assert "no key mentioning" in text


def test_summary_handles_responses_with_no_payout_keys():
    lines = summarize_order_details([{"id": 1, "status": "x"}])
    assert any("no key mentioning" in line for line in lines)


async def test_probe_masks_references_amounts_and_dates_inside_free_text():
    from scripts.shiprocket_orders_probe import mask

    shown = mask("COD remitted on 20 Nov 2026 UTR SBIN0123456789 amount 1,234.00")
    assert shown == "COD remitted on # Nov # UTR <id> amount #,#.#"
    for leaked in ("SBIN0123456789", "2026", "1,234", "20 Nov"):
        assert leaked not in shown
    assert mask("2026-11-20T10:15:00.000000Z") == "#-#-#T#:#:#.#Z"  # format kept, digits gone
    assert mask("Paid") == "Paid"  # a plain status vocabulary stays readable


async def test_probe_output_for_hover_and_date_fields_never_contains_the_values():
    class Client:
        async def request(self, method, path, params=None):
            assert method == "GET"
            if path == "/orders":
                return {"data": [{"id": 7}], "meta": {"pagination": {"total": 1, "total_pages": 1}}}
            return {
                "data": {
                    "cod_recieved_hover_text": "Paid 1,234.00 on 20 Nov 2026 UTR SBIN0123456789",
                    "remittance_date": "20-11-2026 10:15",
                    "remittance_utr": "SBIN0123456789",
                    "remittance_status": "Paid",
                }
            }

    text = "\n".join(await probe(Client(), sample=1, pause=0))
    for leaked in ("SBIN0123456789", "1,234", "20 Nov", "20-11-2026"):
        assert leaked not in text
    assert "'#-#-# #:#'" in text  # the date FORMAT is reported, so it can be confirmed
