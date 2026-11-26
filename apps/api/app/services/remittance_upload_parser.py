"""Parse and validate uploaded COD remittance, gateway settlement and bank
statement reports (CSV / XLSX) into normalised rows.

Pure functions -- no database. The caller stores the original bytes
unchanged and persists only the rows returned here.

## Column recognition (nothing is guessed)

There are no real partner / gateway / bank sample files in this repository,
so column names are NOT invented. A header is recognised automatically only
if it is one of:

1. the OMS's own **template** header for that field (see `TEMPLATES`; the
   upload dialog offers each template for download), or
2. a key the repository's existing integrations already use for that same
   concept -- cited per group below (Shiprocket's remittance normalizer,
   `app/integrations/shiprocket/remittance.py`, whose own docstring notes
   its key names are *not verified against a live account*; Cashfree's
   settlement normalizer, `app/integrations/cashfree/normalizer.py`).

Anything else -- including a bare "Order ID" (Shiprocket uses `order_id`
for ITS order id, not the channel/OMS order number) and every bank-statement
layout -- is never auto-mapped. The user maps those columns explicitly in the
upload dialog (`column_map`), or the file is refused with the headers that
were found and the names that are accepted. Two columns that could be the
same field are reported as ambiguous instead of picking one: a wrong amount
column is a wrong financial entry.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from app.core.exceptions import ValidationError

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000
IST = timedelta(hours=5, minutes=30)
ALLOWED_SUFFIXES = (".csv", ".xlsx")


def _n(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


# field -> (label, template header, repo-evidenced keys)
_COD = {
    "awb": ("AWB number", "AWB", ["awb_code", "awb", "awb_number", "tracking_number"]),
    "order_ref": (
        "OMS order number",
        "OMS Order ID",
        ["channel_order_id", "order_number", "channel_order_no", "reference_order_id"],
    ),
    "remittance_reference": (
        "Remittance reference / UTR",
        "Remittance Reference",
        ["utr", "utr_no", "remittance_utr", "crf_id", "remittance_id", "reference"],
    ),
    "remittance_amount": (
        "Remittance amount",
        "Remittance Amount",
        ["remitted_amount", "remittance_amount", "amount_remitted", "remitted_cod_amount"],
    ),
    "cod_amount": (
        "COD amount (optional)",
        "COD Amount",
        ["cod_amount", "cod_payable", "order_cod_amount", "cod_charges_amount"],
    ),
    "remittance_date": (
        "Remittance date",
        "Remittance Date",
        ["remittance_date", "remitted_at", "settlement_date", "paid_date", "payment_date"],
    ),
}
# Cashfree settlement keys used by the repository's settlement normalizer.
_PREPAID = {
    "gateway_transaction_id": (
        "Gateway transaction ID",
        "Gateway Transaction ID",
        ["cf_payment_id"],
    ),
    "order_ref": ("OMS order number", "OMS Order ID", ["order_number"]),
    "settlement_reference": (
        "Settlement reference / UTR",
        "Settlement Reference",
        ["settlement_utr"],
    ),
    "gross_amount": ("Gross amount", "Gross Amount", ["payment_amount"]),
    "gateway_charges": ("Gateway charges", "Gateway Charges", ["pg_service_charge"]),
    "gst_amount": ("GST on charges", "GST Amount", ["pg_service_tax"]),
    "refund_amount": ("Refund amount", "Refund Amount", []),
    "net_amount": ("Net settlement amount", "Net Settlement Amount", ["amount_settled"]),
    "transaction_date": ("Transaction date", "Transaction Date", []),
    "settlement_date": (
        "Settlement date",
        "Settlement Date",
        ["settlement_processed_on", "settlement_initiated_on"],
    ),
    "line_status": ("Status", "Status", []),
}
# No bank-statement format is documented anywhere in the repository.
_BANK = {
    "credit_date": ("Credit date", "Credit Date", []),
    "reference": ("Reference / UTR", "Reference", []),
    "amount": ("Credit amount", "Credit Amount", []),
    "narration": ("Narration", "Narration", []),
}

# Shiprocket COD payout (CRF cycle) report. Shiprocket publishes no column
# list for its dashboard export, so only OMS template headers and keys already
# used by this repository map automatically; anything else is mapped by hand.
_SR_PAYOUT = {
    "crf_id": ("CRF / payout ID", "CRF ID", ["crf_id", "remittance_id"]),
    "utr": ("UTR", "UTR", ["utr_no", "remittance_utr"]),
    "payout_date": ("Payout date", "Payout Date", ["remittance_date"]),
    "status": ("Status", "Status", ["remittance_status"]),
    "cod_amount": ("COD amount (optional)", "COD Amount", ["cod_amount"]),
    "freight_deduction": ("Freight deducted (optional)", "Freight Deduction", []),
    "other_deductions": ("Other deductions (optional)", "Other Deductions", []),
    "net_amount": (
        "Remittance amount",
        "Remittance Amount",
        ["remittance_amount", "remitted_amount"],
    ),
}
# Wallet passbook export. The extra keys are the field names of Shiprocket's
# documented statement API.
_SR_WALLET = {
    "transaction_id": ("Transaction ID", "Transaction ID", ["transaction_id"]),
    "created_at": ("Date", "Date", ["created_at"]),
    "action": ("Type / action", "Action", ["action"]),
    "debit_amount": ("Debit amount", "Debit Amount", ["debit_amount"]),
    "credit_amount": ("Credit amount", "Credit Amount", ["credit_amount"]),
    "balance_amount": ("Balance", "Balance Amount", ["balance_amount"]),
    "awb_code": ("AWB", "AWB", ["awb_code"]),
    "channel_order_id": ("Order number", "Order Number", ["channel_order_id"]),
    "description": ("Description", "Description", ["description"]),
}

SPECS = {
    "cod": _COD,
    "prepaid": _PREPAID,
    "bank": _BANK,
    "sr_payout": _SR_PAYOUT,
    "sr_wallet": _SR_WALLET,
}
# Each inner list: at least one of those fields must be mapped.
REQUIRED_GROUPS = {
    "cod": [["remittance_amount"], ["awb", "order_ref"]],
    "prepaid": [["net_amount"], ["gateway_transaction_id", "order_ref"]],
    "bank": [["amount"]],
    "sr_payout": [["net_amount"], ["crf_id", "utr"]],
    "sr_wallet": [["created_at"], ["debit_amount", "credit_amount"]],
}


def aliases_for(kind: str) -> dict[str, list[str]]:
    """field -> normalised accepted header names (template header + repo keys)."""
    return {
        f: [_n(template), *(_n(k) for k in keys)]
        for f, (_label, template, keys) in SPECS[kind].items()
    }


def templates(kind: str) -> list[str]:
    return [template for _label, template, _keys in SPECS[kind].values()]


def field_label(kind: str, field_name: str) -> str:
    return SPECS[kind][field_name][0]


@dataclass
class HeaderAnalysis:
    kind: str
    headers: list[str]
    header_row: int
    mapping: dict[str, int] = field(default_factory=dict)  # field -> column index
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)  # human-readable group labels
    ignored: list[str] = field(default_factory=list)
    sample_rows: list[list[str]] = field(default_factory=list)

    @property
    def mapped_headers(self) -> dict[str, str]:
        return {f: self.headers[i] for f, i in self.mapping.items()}

    @property
    def problems(self) -> list[str]:
        out = []
        for f, names in self.ambiguous.items():
            out.append(
                f"Several columns could be '{field_label(self.kind, f)}': "
                + ", ".join(f"'{n}'" for n in names)
                + ". Choose the right one in the column mapping."
            )
        for group in self.missing:
            out.append(f"No column was identified for: {group}.")
        return out


@dataclass
class ParsedFile:
    rows: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    total_rows: int = 0
    skipped: int = 0
    column_mapping: dict[str, str] = field(default_factory=dict)
    ignored_columns: list[str] = field(default_factory=list)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dedupe_key(*parts: object) -> str:
    text = "|".join("" if p is None else str(p).strip().lower() for p in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_file(filename: str, data: bytes) -> None:
    if not filename.lower().endswith(ALLOWED_SUFFIXES):
        raise ValidationError("Upload a .csv or .xlsx file.")
    if not data:
        raise ValidationError("The uploaded file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")


def _read_table(filename: str, data: bytes) -> list[list[object]]:
    if filename.lower().endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        return [list(r) for r in csv.reader(io.StringIO(text))]
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 -- any parse failure is a user-facing 422
        raise ValidationError("The .xlsx file could not be read.") from exc
    ws = wb.worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def parse_money(value: object) -> Decimal | None:
    """`None` for blank; raises `ValueError` for text that is not a number."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    text = str(value).strip()
    if text in ("", "-", "--"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[₹,\s]|rs\.?|inr", "", text, flags=re.I).strip("()")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"'{value}' is not a valid amount") from exc
    if not amount.is_finite():
        raise ValueError(f"'{value}' is not a valid amount")
    return (-amount if negative else amount).quantize(Decimal("0.01"))


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d/%m/%y",
)


def parse_date(value: object) -> datetime | None:
    """A UTC-aware datetime. Report dates carry no zone, so they are read as
    IST wall-clock (this OMS's business time zone). `None` for blank;
    `ValueError` for unreadable text.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime):
        naive = value.replace(tzinfo=None) if value.tzinfo is None else None
        if naive is None:
            return value.astimezone(UTC)
        return (naive - IST).replace(tzinfo=UTC)
    if isinstance(value, date):
        return (datetime(value.year, value.month, value.day) - IST).replace(tzinfo=UTC)
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)  # noqa: DTZ007 -- zone applied below
        except ValueError:
            continue
        return (parsed - IST).replace(tzinfo=UTC)
    try:
        iso = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"'{value}' is not a recognised date") from exc
    if iso.tzinfo is None:
        return (iso - IST).replace(tzinfo=UTC)
    return iso.astimezone(UTC)


def _clean(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():  # Excel numeric ids
        text = text[:-2]
    return text[:limit] or None


def _header_text(cell: object) -> str:
    return str(cell).strip() if cell is not None else ""


def analyze_headers(
    kind: str,
    filename: str,
    data: bytes,
    column_map: dict[str, str] | None = None,
) -> tuple[HeaderAnalysis, list[list[object]]]:
    """Find the header row and map columns to fields.

    Automatic mapping uses only template headers and repo-evidenced keys.
    `column_map` (field -> exact header text, case-insensitive) is the user's
    explicit choice and overrides detection; an unknown header is an error.
    """
    validate_file(filename, data)
    table = _read_table(filename, data)
    aliases = aliases_for(kind)
    all_alias = {a for names in aliases.values() for a in names}

    def recognised(row: list[object]) -> int:
        return sum(1 for c in row if _n(_header_text(c)) in all_alias)

    header_idx = next((i for i, row in enumerate(table[:25]) if recognised(row) > 0), None)
    if header_idx is None:  # unrecognised layout: the first non-empty row is the header
        header_idx = next(
            (i for i, row in enumerate(table[:25]) if any(_header_text(c) for c in row)), None
        )
    if header_idx is None:
        raise ValidationError("The file has no header row.")
    headers = [_header_text(c) for c in table[header_idx]]
    analysis = HeaderAnalysis(kind=kind, headers=headers, header_row=header_idx)
    body = table[header_idx + 1 :]
    analysis.sample_rows = [[_header_text(c) for c in r] for r in body[:3]]

    normalised = [_n(h) for h in headers]
    mapped_cols: set[int] = set()
    for f_name, names in aliases.items():
        explicit = (column_map or {}).get(f_name)
        if explicit is not None:
            if explicit == "":
                continue  # the user said "not in this file"
            matches = [
                i for i, h in enumerate(headers) if h.strip().lower() == explicit.strip().lower()
            ]
            if not matches:
                raise ValidationError(
                    f"The column '{explicit}' chosen for '{field_label(kind, f_name)}' is not in "
                    f"the file. Columns found: {', '.join(h for h in headers if h) or 'none'}."
                )
            analysis.mapping[f_name] = matches[0]
            mapped_cols.add(matches[0])
            continue
        hits = [i for i, n in enumerate(normalised) if n and n in names]
        if len(hits) == 1:
            analysis.mapping[f_name] = hits[0]
            mapped_cols.add(hits[0])
        elif len(hits) > 1:
            analysis.ambiguous[f_name] = [headers[i] for i in hits]
    # A column claimed by two fields is ambiguous too (e.g. one "Reference" header
    # accepted for two different fields).
    owner: dict[int, list[str]] = {}
    for f_name, idx in analysis.mapping.items():
        owner.setdefault(idx, []).append(f_name)
    for idx, fields in owner.items():
        if len(fields) > 1 and not any((column_map or {}).get(f) for f in fields):
            for f_name in fields:
                analysis.mapping.pop(f_name, None)
                analysis.ambiguous[f_name] = [headers[idx]]
    for group in REQUIRED_GROUPS[kind]:
        if not any(f in analysis.mapping for f in group):
            analysis.missing.append(" or ".join(field_label(kind, f) for f in group))
    used = set(analysis.mapping.values())
    analysis.ignored = [h for i, h in enumerate(headers) if h and i not in used]
    return analysis, table


def _raise_if_unusable(analysis: HeaderAnalysis, kind: str) -> None:
    if not analysis.problems:
        return
    accepted = "; ".join(
        f"{field_label(kind, f)}: {', '.join(templates(kind)[i : i + 1])}"
        for i, f in enumerate(SPECS[kind])
        if any(f in group for group in REQUIRED_GROUPS[kind])
    )
    found = ", ".join(h for h in analysis.headers if h) or "none"
    raise ValidationError(
        " ".join(analysis.problems)
        + f" Columns found in the file: {found}. Accepted headers are the OMS template headers "
        + f"({accepted}) plus the keys this system already uses for the same data. "
        + "Download the template, or map the columns yourself in the upload dialog."
    )


def _get(row: list[object], idx: dict[str, int], name: str) -> object:
    i = idx.get(name)
    return row[i] if i is not None and i < len(row) else None


def _prepare(kind: str, filename: str, data: bytes, column_map: dict[str, str] | None):
    analysis, table = analyze_headers(kind, filename, data, column_map)
    _raise_if_unusable(analysis, kind)
    body = table[analysis.header_row + 1 :]
    if len(body) > MAX_ROWS:
        raise ValidationError(f"The file has more than {MAX_ROWS} rows.")
    out = ParsedFile(column_mapping=analysis.mapped_headers, ignored_columns=analysis.ignored)
    return analysis, body, out


def parse_cod_remittances(
    filename: str, data: bytes, partner: str, column_map: dict[str, str] | None = None
) -> ParsedFile:
    analysis, body, out = _prepare("cod", filename, data, column_map)
    idx = analysis.mapping
    seen: set[str] = set()
    for offset, row in enumerate(body):
        line_no = analysis.header_row + offset + 2
        if not any(str(c or "").strip() for c in row):
            continue
        out.total_rows += 1
        try:
            amount = parse_money(_get(row, idx, "remittance_amount"))
            cod_amount = parse_money(_get(row, idx, "cod_amount"))
            remitted_on = parse_date(_get(row, idx, "remittance_date"))
        except ValueError as exc:
            out.errors.append({"row": line_no, "error": str(exc)})
            continue
        awb = _clean(_get(row, idx, "awb"), 64)
        order_ref = _clean(_get(row, idx, "order_ref"), 64)
        if amount is None or amount == 0:
            out.errors.append({"row": line_no, "error": "Remittance amount is missing or zero."})
            continue
        if not awb and not order_ref:
            out.errors.append({"row": line_no, "error": "Neither AWB nor order reference given."})
            continue
        reference = _clean(_get(row, idx, "remittance_reference"), 128)
        key = dedupe_key(
            partner,
            awb,
            order_ref,
            reference,
            amount,
            remitted_on.date().isoformat() if remitted_on else "",
        )
        if key in seen:
            out.skipped += 1
            continue
        seen.add(key)
        out.rows.append(
            {
                "awb": awb,
                "order_ref": order_ref,
                "remittance_reference": reference,
                "remittance_amount": amount,
                "cod_amount_reported": cod_amount,
                "remittance_date": remitted_on,
                "dedupe_key": key,
                "raw_row": {str(i): _jsonable(c) for i, c in enumerate(row)},
            }
        )
    return out


def parse_gateway_settlements(
    filename: str, data: bytes, gateway: str, column_map: dict[str, str] | None = None
) -> ParsedFile:
    analysis, body, out = _prepare("prepaid", filename, data, column_map)
    idx = analysis.mapping
    seen: set[str] = set()
    for offset, row in enumerate(body):
        line_no = analysis.header_row + offset + 2
        if not any(str(c or "").strip() for c in row):
            continue
        out.total_rows += 1
        try:
            net = parse_money(_get(row, idx, "net_amount"))
            gross = parse_money(_get(row, idx, "gross_amount"))
            charges = parse_money(_get(row, idx, "gateway_charges")) or Decimal("0.00")
            gst = parse_money(_get(row, idx, "gst_amount")) or Decimal("0.00")
            refund = parse_money(_get(row, idx, "refund_amount")) or Decimal("0.00")
            txn_date = parse_date(_get(row, idx, "transaction_date"))
            settled_on = parse_date(_get(row, idx, "settlement_date"))
        except ValueError as exc:
            out.errors.append({"row": line_no, "error": str(exc)})
            continue
        txn_id = _clean(_get(row, idx, "gateway_transaction_id"), 128)
        order_ref = _clean(_get(row, idx, "order_ref"), 64)
        if net is None:
            out.errors.append({"row": line_no, "error": "Net settlement amount is missing."})
            continue
        if not txn_id and not order_ref:
            out.errors.append(
                {"row": line_no, "error": "Neither transaction id nor order id given."}
            )
            continue
        status = _clean(_get(row, idx, "line_status"), 32)
        ref = _clean(_get(row, idx, "settlement_reference"), 128)
        key = dedupe_key(gateway, txn_id, order_ref, ref, net, gross, refund)
        if key in seen:
            out.skipped += 1
            continue
        seen.add(key)
        out.rows.append(
            {
                "gateway_transaction_id": txn_id,
                "order_ref": order_ref,
                "settlement_reference": ref,
                "gross_amount": gross,
                "gateway_charges": charges,
                "gst_amount": gst,
                "refund_amount": refund,
                "net_amount": net,
                "transaction_date": txn_date,
                "settlement_date": settled_on,
                "line_status": status.lower() if status else None,
                "dedupe_key": key,
                "raw_row": {str(i): _jsonable(c) for i, c in enumerate(row)},
            }
        )
    return out


def parse_shiprocket_payouts(
    filename: str, data: bytes, column_map: dict[str, str] | None = None
) -> ParsedFile:
    analysis, body, out = _prepare("sr_payout", filename, data, column_map)
    idx = analysis.mapping
    seen: set[str] = set()
    for offset, row in enumerate(body):
        line_no = analysis.header_row + offset + 2
        if not any(str(c or "").strip() for c in row):
            continue
        out.total_rows += 1
        try:
            money = {
                name: parse_money(_get(row, idx, name))
                for name in ("cod_amount", "freight_deduction", "other_deductions", "net_amount")
            }
            paid_on = parse_date(_get(row, idx, "payout_date"))
        except ValueError as exc:
            out.errors.append({"row": line_no, "error": str(exc)})
            continue
        crf = _clean(_get(row, idx, "crf_id"), 64)
        utr = _clean(_get(row, idx, "utr"), 128)
        if money["net_amount"] is None:
            out.errors.append({"row": line_no, "error": "Remittance amount is missing."})
            continue
        if not crf and not utr:
            out.errors.append({"row": line_no, "error": "Neither a CRF / payout ID nor a UTR."})
            continue
        # One payout per CRF id; without one, per UTR.
        key = dedupe_key("sr_payout", "crf" if crf else "utr", crf or utr)
        if key in seen:
            out.errors.append({"row": line_no, "error": "This payout appears twice in the file."})
            continue
        seen.add(key)
        out.rows.append(
            {
                "dedupe_key": key,
                "crf_id": crf,
                "utr": utr,
                "payout_date": paid_on,
                "status": _clean(_get(row, idx, "status"), 64),
                **money,
                "raw_row": {str(i): _jsonable(c) for i, c in enumerate(row)},
            }
        )
    return out


def parse_shiprocket_wallet(
    filename: str, data: bytes, column_map: dict[str, str] | None = None
) -> ParsedFile:
    """Rows keyed by the documented statement-API field names, so the same
    normaliser (and duplicate keys) serve both the API and the report."""
    analysis, body, out = _prepare("sr_wallet", filename, data, column_map)
    idx = analysis.mapping
    for offset, row in enumerate(body):
        line_no = analysis.header_row + offset + 2
        if not any(str(c or "").strip() for c in row):
            continue
        out.total_rows += 1
        try:
            when = parse_date(_get(row, idx, "created_at"))
            amounts = {
                name: parse_money(_get(row, idx, name))
                for name in ("debit_amount", "credit_amount", "balance_amount")
            }
        except ValueError as exc:
            out.errors.append({"row": line_no, "error": str(exc)})
            continue
        if when is None:
            out.errors.append({"row": line_no, "error": "Date is missing."})
            continue
        record: dict = {"created_at": when.isoformat(), "_row": line_no}
        for name, value in amounts.items():
            record[name] = None if value is None else str(value)
        for name in ("transaction_id", "action", "awb_code", "channel_order_id", "description"):
            record[name] = _clean(_get(row, idx, name), 500)
        out.rows.append(record)
    return out


def parse_bank_credits(
    filename: str, data: bytes, column_map: dict[str, str] | None = None
) -> ParsedFile:
    analysis, body, out = _prepare("bank", filename, data, column_map)
    idx = analysis.mapping
    seen: set[str] = set()
    for offset, row in enumerate(body):
        line_no = analysis.header_row + offset + 2
        if not any(str(c or "").strip() for c in row):
            continue
        out.total_rows += 1
        try:
            amount = parse_money(_get(row, idx, "amount"))
            credited_on = parse_date(_get(row, idx, "credit_date"))
        except ValueError as exc:
            out.errors.append({"row": line_no, "error": str(exc)})
            continue
        if amount is None or amount <= 0:
            out.skipped += 1  # debits / blank credit cells are not credits
            continue
        reference = _clean(_get(row, idx, "reference"), 128)
        narration = _clean(_get(row, idx, "narration"), 500)
        key = dedupe_key(
            credited_on.date().isoformat() if credited_on else "", amount, reference, narration
        )
        if key in seen:
            out.skipped += 1
            continue
        seen.add(key)
        out.rows.append(
            {
                "credit_date": credited_on,
                "amount": amount,
                "reference": reference,
                "narration": narration,
                "dedupe_key": key,
                "raw_row": {str(i): _jsonable(c) for i, c in enumerate(row)},
            }
        )
    return out


def template_csv(kind: str) -> bytes:
    """Header-only CSV in the OMS template layout (no sample data)."""
    return (",".join(templates(kind)) + "\n").encode("utf-8")


def _jsonable(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
