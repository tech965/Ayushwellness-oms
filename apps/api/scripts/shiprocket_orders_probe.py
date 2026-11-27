"""READ-ONLY probe: what Shiprocket's DOCUMENTED order endpoints return for COD orders.

Answers, from a real account, the questions the public documentation leaves open:
which payout fields are populated, what `remittance_status` actually contains, and
whether an undocumented `cod_recieved_hover_text`-style field exists.

Run from apps/api on a machine that already has the Shiprocket credentials configured
(the same settings the app uses; nothing is read from or written to the database):

    python scripts/shiprocket_orders_probe.py --sample 25

It makes only GET calls, with a short pause between them:
  * GET /orders?filter_by=payment_method&filter=cod&per_page=N   (documented list + filter)
  * GET /orders/show/{id}                                         (documented order detail)

It prints key names and counts, plus the DISTINCT values of `remittance_status`,
`payment_status` and any `*hover*` text. It never prints a token, a password, customer
details, a UTR, a date or an amount.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PAYOUT_KEY = re.compile(r"remit|payout|utr|crf|settle|hover|paid|cod", re.I)
VALUE_KEYS_SHOWN = ("remittance_status", "payment_status")  # small vocabularies, not PII
MAX_DISTINCT = 10
PAUSE_SECONDS = 0.4


def _filled(value: Any) -> bool:
    return value not in (None, "", "NA", "N/A", "None") and str(value).strip() != ""


def mask(text: str) -> str:
    """Keeps the SHAPE of a value, never its content: long identifiers (UTRs, CRF ids,
    references) become `<id>` and every digit run becomes `#`, so an amount, a date or
    a reference cannot be read back from the output."""
    text = re.sub(r"[A-Za-z0-9_\-/]*\d[A-Za-z0-9_\-/]*", _mask_token, text)
    return text[:120]


def _mask_token(match: re.Match[str]) -> str:
    token = match.group(0)
    letters = len(re.findall(r"[A-Za-z]", token))
    digits = len(re.findall(r"\d", token))
    if letters >= 2 and digits >= 4:  # reference-like (UTR, CRF id); an ISO date has one "T"
        return "<id>"
    return re.sub(r"\d+", "#", token)


def _items(body: Any) -> list[dict[str, Any]]:
    data = body.get("data") if isinstance(body, dict) else None
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def summarize_order_details(details: list[dict[str, Any]]) -> list[str]:
    """Lines describing the `data` objects of several `orders/show` responses."""
    lines: list[str] = [f"order-detail responses read: {len(details)}"]
    keys: Counter[str] = Counter()
    filled: Counter[str] = Counter()
    distinct: dict[str, Counter[str]] = {}
    for data in details:
        for key, value in data.items():
            if not PAYOUT_KEY.search(key):
                continue
            keys[key] += 1
            if _filled(value):
                filled[key] += 1
                text = str(value)
                # Only masked SHAPES are ever shown: small vocabularies and free-text
                # (hover) values, plus the format of any date, never a real value.
                if key in VALUE_KEYS_SHOWN or "hover" in key.lower() or "date" in key.lower():
                    distinct.setdefault(key, Counter())[mask(text)] += 1
    if not keys:
        lines.append("no key mentioning remit/payout/utr/crf/settle/hover/paid/cod was returned")
    for key in sorted(keys):
        lines.append(
            f"  data.{key}: present in {keys[key]}/{len(details)}, non-empty in {filled[key]}"
        )
        for value, n in (distinct.get(key) or Counter()).most_common(MAX_DISTINCT):
            lines.append(f"      value {value!r} x{n}")
    return lines


async def probe(client: Any, *, sample: int, pause: float = PAUSE_SECONDS) -> list[str]:
    """`client` needs one method: `await client.request("GET", path, params=...)`."""
    sample = max(1, min(sample, 50))
    out: list[str] = []
    listing = await client.request(
        "GET",
        "/orders",
        params={"filter_by": "payment_method", "filter": "cod", "per_page": sample, "page": 1},
    )
    meta = listing.get("meta", {}).get("pagination", {}) if isinstance(listing, dict) else {}
    out.append(f"list: pagination keys={sorted(meta)}")
    out.append(
        f"list: total COD orders reported={meta.get('total')} pages={meta.get('total_pages')}"
    )
    rows = _items(listing)
    out.append(f"list: orders returned={len(rows)}")
    if rows:
        list_keys = sorted({k for r in rows for k in r if PAYOUT_KEY.search(k)})
        out.append(f"list: item keys mentioning payout/cod terms={list_keys}")
        methods = Counter(str(r.get("payment_method")) for r in rows)
        out.append(f"list: payment_method values={dict(methods)}")
    details: list[dict[str, Any]] = []
    for row in rows[:sample]:
        order_id = row.get("id")
        if order_id is None:
            continue
        time.sleep(pause)
        body = await client.request("GET", f"/orders/show/{order_id}")
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            details.append(data)
    out.extend(summarize_order_details(details))
    return out


async def _main(sample: int) -> int:
    from app.integrations.registry import get_adapter
    from app.integrations.shiprocket.adapter import ShiprocketAdapter
    from app.models.integration import IntegrationCode

    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    if not isinstance(adapter, ShiprocketAdapter) or not adapter._configured:  # noqa: SLF001
        print("Shiprocket is not configured on this machine; nothing was called.")
        return 1
    try:
        lines = await probe(adapter._get_client(), sample=sample)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - never echo request details
        print(f"Probe failed ({type(exc).__name__}); nothing further was called.")
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sample", type=int, default=25, help="COD orders to read (max 50)")
    sys.exit(asyncio.run(_main(parser.parse_args().sample)))
