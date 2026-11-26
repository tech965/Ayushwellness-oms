"""Settlement-file parsing and validation (Modules 6 & 7).

Columns are recognised only from the OMS template headers and keys the
repository's own integrations already use -- never guessed. Anything else
must be mapped explicitly, and ambiguity is an error, not a coin toss.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from app.core.exceptions import ValidationError
from app.services import remittance_upload_parser as p
from openpyxl import Workbook


def csv_bytes(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_money_formats():
    assert p.parse_money("₹1,234.50") == Decimal("1234.50")
    assert p.parse_money("Rs. 99") == Decimal("99.00")
    assert p.parse_money("(500)") == Decimal("-500.00")
    assert p.parse_money(12.5) == Decimal("12.50")
    assert p.parse_money("") is None
    assert p.parse_money(None) is None
    with pytest.raises(ValueError):
        p.parse_money("abc")
    with pytest.raises(ValueError):
        p.parse_money("NaN")


def test_date_formats_are_read_as_ist():
    a = p.parse_date("25-11-2026")
    assert (a.year, a.month, a.day, a.hour, a.minute) == (2026, 11, 24, 18, 30)  # IST midnight
    assert p.parse_date("2026-11-25").day == 24
    assert p.parse_date("25-Nov-2026") == a
    assert p.parse_date("") is None
    with pytest.raises(ValueError):
        p.parse_date("not a date")


def test_cod_template_headers_and_row_errors():
    data = csv_bytes(
        "AWB,OMS Order ID,Remittance Reference,Remittance Amount,Remittance Date",
        'AWB1,100,R1,"1,000.00",25-11-2026',
        "AWB2,101,R1,600,25-11-2026",
        ",,R2,500,25-11-2026",  # no AWB / order ref -> rejected
        "AWB3,102,R3,abc,25-11-2026",  # bad amount -> rejected
        "AWB4,103,R4,0,25-11-2026",  # zero -> rejected
    )
    out = p.parse_cod_remittances("r.csv", data, "Delhivery")
    assert [r["awb"] for r in out.rows] == ["AWB1", "AWB2"]
    assert out.rows[0]["remittance_amount"] == Decimal("1000.00")
    assert out.rows[0]["remittance_reference"] == "R1"
    assert out.rows[0]["order_ref"] == "100"
    assert len(out.errors) == 3
    assert {e["row"] for e in out.errors} == {4, 5, 6}
    assert out.column_mapping["awb"] == "AWB"
    assert out.ignored_columns == []


def test_cod_repo_evidenced_keys_are_recognised():
    """The keys Shiprocket's remittance normalizer already reads."""
    data = csv_bytes(
        "awb_code,channel_order_id,crf_id,remitted_amount,cod_amount,remittance_date",
        "AWB9,500,CRF1,250.5,300,25-11-2026",
    )
    out = p.parse_cod_remittances("r.csv", data, "Shiprocket")
    row = out.rows[0]
    assert row["awb"] == "AWB9" and row["order_ref"] == "500"
    assert row["remittance_reference"] == "CRF1"
    assert row["remittance_amount"] == Decimal("250.50")
    assert row["cod_amount_reported"] == Decimal("300.00")


def test_bare_order_id_is_not_guessed_but_can_be_mapped():
    """Shiprocket's `order_id` is ITS order id, so 'Order ID' alone is not
    assumed to be the OMS order number."""
    data = csv_bytes("Order ID,Remittance Amount", "ORD-1,100")
    with pytest.raises(ValidationError) as exc:
        p.parse_cod_remittances("r.csv", data, "P")
    message = str(exc.value)
    assert "AWB number or OMS order number" in message
    assert "Order ID" in message  # shows the headers it did find
    assert "OMS Order ID" in message  # and what is accepted
    out = p.parse_cod_remittances("r.csv", data, "P", column_map={"order_ref": "Order ID"})
    assert out.rows[0]["order_ref"] == "ORD-1"
    assert out.column_mapping["order_ref"] == "Order ID"


def test_ambiguous_columns_are_refused_not_guessed():
    data = csv_bytes("AWB,Remittance Amount,Amount Remitted", "A1,100,90")
    with pytest.raises(ValidationError) as exc:
        p.parse_cod_remittances("r.csv", data, "P")
    assert "Several columns could be 'Remittance amount'" in str(exc.value)
    assert "'Remittance Amount'" in str(exc.value) and "'Amount Remitted'" in str(exc.value)
    # The user's explicit choice resolves it.
    out = p.parse_cod_remittances(
        "r.csv", data, "P", column_map={"remittance_amount": "Amount Remitted"}
    )
    assert out.rows[0]["remittance_amount"] == Decimal("90.00")
    assert "Remittance Amount" in out.ignored_columns


def test_duplicate_header_text_is_ambiguous():
    data = csv_bytes("AWB,Remittance Amount,Remittance Amount", "A1,100,90")
    with pytest.raises(ValidationError) as exc:
        p.parse_cod_remittances("r.csv", data, "P")
    assert "Several columns" in str(exc.value)


def test_explicit_mapping_must_name_a_real_column():
    data = csv_bytes("AWB,Remittance Amount", "A1,100")
    with pytest.raises(ValidationError) as exc:
        p.parse_cod_remittances("r.csv", data, "P", column_map={"remittance_amount": "Nope"})
    assert "'Nope'" in str(exc.value) and "Columns found" in str(exc.value)


def test_unmapped_optional_field_can_be_declared_absent():
    data = csv_bytes("AWB,Remittance Amount,Remittance Date", "A1,100,25-11-2026")
    out = p.parse_cod_remittances("r.csv", data, "P", column_map={"remittance_date": ""})
    assert out.rows[0]["remittance_date"] is None


def test_cod_duplicate_lines_in_one_file_are_counted_once():
    data = csv_bytes(
        "AWB,Remittance Amount,Remittance Date,Remittance Reference",
        "AWB1,100,25-11-2026,R1",
        "AWB1,100,25-11-2026,R1",
    )
    out = p.parse_cod_remittances("r.csv", data, "P")
    assert len(out.rows) == 1
    assert out.skipped == 1


def test_missing_required_column_is_refused_with_names():
    with pytest.raises(ValidationError) as exc:
        p.parse_cod_remittances("r.csv", csv_bytes("AWB,Foo", "A,1"), "P")
    assert "Remittance amount" in str(exc.value)
    assert "Columns found in the file: AWB, Foo" in str(exc.value)
    assert "template" in str(exc.value).lower()
    with pytest.raises(ValidationError):
        p.parse_cod_remittances(
            "r.csv", csv_bytes("Remittance Amount,Remittance Reference", "10,R"), "P"
        )


def test_file_validation_limits():
    with pytest.raises(ValidationError):
        p.validate_file("x.pdf", b"abc")
    with pytest.raises(ValidationError):
        p.validate_file("x.csv", b"")
    with pytest.raises(ValidationError):
        p.validate_file("x.csv", b"a" * (p.MAX_FILE_BYTES + 1))


def test_xlsx_is_parsed():
    wb = Workbook()
    ws = wb.active
    ws.append(["AWB", "Remittance Amount", "Remittance Reference"])
    ws.append(["AWB9", 250.5, "R9"])
    buf = io.BytesIO()
    wb.save(buf)
    out = p.parse_cod_remittances("r.xlsx", buf.getvalue(), "P")
    assert out.rows[0]["awb"] == "AWB9"
    assert out.rows[0]["remittance_amount"] == Decimal("250.50")


def test_corrupt_xlsx_is_a_validation_error():
    with pytest.raises(ValidationError):
        p.parse_cod_remittances("r.xlsx", b"not really an xlsx", "P")


def test_gateway_template_headers():
    data = csv_bytes(
        "Gateway Transaction ID,OMS Order ID,Gross Amount,Gateway Charges,GST Amount,"
        "Refund Amount,Net Settlement Amount,Settlement Reference,Settlement Date",
        "T1,100,1000,23.60,4.25,0,972.15,UTR1,25-11-2026",
        "T2,101,500,,,100,400,UTR1,25-11-2026",
        "T3,,300,,,0,,UTR1,25-11-2026",  # net missing -> rejected
    )
    out = p.parse_gateway_settlements("g.csv", data, "Cashfree")
    assert len(out.rows) == 2 and len(out.errors) == 1
    first = out.rows[0]
    assert first["gross_amount"] == Decimal("1000.00")
    assert first["gateway_charges"] == Decimal("23.60")
    assert first["gst_amount"] == Decimal("4.25")
    assert first["net_amount"] == Decimal("972.15")
    assert out.rows[1]["gateway_charges"] == Decimal("0.00")
    assert out.rows[1]["refund_amount"] == Decimal("100.00")


def test_gateway_cashfree_repo_keys_are_recognised():
    """The keys the Cashfree settlement normalizer already reads."""
    data = csv_bytes(
        "cf_payment_id,payment_amount,pg_service_charge,pg_service_tax,amount_settled,"
        "settlement_utr,settlement_processed_on",
        "111,1000,20,3.6,976.4,UTR7,2026-11-25",
    )
    out = p.parse_gateway_settlements("g.csv", data, "Cashfree")
    row = out.rows[0]
    assert row["gateway_transaction_id"] == "111"
    assert row["gross_amount"] == Decimal("1000.00")
    assert row["gateway_charges"] == Decimal("20.00") and row["gst_amount"] == Decimal("3.60")
    assert row["net_amount"] == Decimal("976.40") and row["settlement_reference"] == "UTR7"


def test_bank_statement_template_keeps_credits_and_skips_debits():
    data = csv_bytes(
        "Credit Date,Narration,Debit,Credit Amount,Reference",
        "25-11-2026,NEFT Delhivery,,1000,R1",
        "25-11-2026,Vendor payment,500,,",
        "26-11-2026,NEFT Cashfree,,2500.50,UTR2",
    )
    out = p.parse_bank_credits("b.csv", data)
    assert [r["amount"] for r in out.rows] == [Decimal("1000.00"), Decimal("2500.50")]
    assert out.skipped == 1  # the debit row is not a credit
    assert out.rows[0]["reference"] == "R1"
    assert out.ignored_columns == ["Debit"]  # reported, not silently dropped


def test_unrecognised_bank_layout_needs_explicit_mapping():
    """No bank format is documented in the repository, so none is assumed."""
    data = csv_bytes(
        "Txn Date,Description,Withdrawal,Deposit,Chq/Ref No",
        "25-11-2026,NEFT,,1000,R1",
    )
    with pytest.raises(ValidationError) as exc:
        p.parse_bank_credits("b.csv", data)
    assert "Credit amount" in str(exc.value)
    assert "Txn Date, Description, Withdrawal, Deposit, Chq/Ref No" in str(exc.value)
    out = p.parse_bank_credits(
        "b.csv",
        data,
        column_map={
            "amount": "Deposit",
            "credit_date": "Txn Date",
            "reference": "Chq/Ref No",
            "narration": "Description",
        },
    )
    assert out.rows[0]["amount"] == Decimal("1000.00") and out.rows[0]["reference"] == "R1"
    assert out.ignored_columns == ["Withdrawal"]


def test_header_analysis_for_the_preview_screen():
    data = csv_bytes(
        "Some title row",
        "AWB,Foo,Remittance Amount",
        "A1,x,100",
        "A2,y,200",
    )
    analysis, _ = p.analyze_headers("cod", "r.csv", data)
    assert analysis.header_row == 1  # skipped the title row
    assert analysis.mapped_headers == {"awb": "AWB", "remittance_amount": "Remittance Amount"}
    assert analysis.ignored == ["Foo"]
    assert analysis.sample_rows == [["A1", "x", "100"], ["A2", "y", "200"]]
    assert analysis.problems == []
    bad, _ = p.analyze_headers("cod", "r.csv", csv_bytes("Foo,Bar", "1,2"))
    assert bad.problems and bad.headers == ["Foo", "Bar"]


def test_templates_round_trip_through_the_parser():
    for kind, parse in (("cod", p.parse_cod_remittances), ("prepaid", p.parse_gateway_settlements)):
        header = p.template_csv(kind).decode().strip().split(",")
        analysis, _ = p.analyze_headers(kind, "t.csv", p.template_csv(kind))
        assert analysis.problems == [], kind
        assert set(analysis.mapping) == set(p.SPECS[kind]), kind
        assert header == p.templates(kind)
        assert parse is not None
    bank, _ = p.analyze_headers("bank", "t.csv", p.template_csv("bank"))
    assert bank.problems == []


def test_dedupe_key_is_stable_and_case_insensitive():
    assert p.dedupe_key("A", "x ", 1) == p.dedupe_key("a", "X", 1)
    assert p.dedupe_key("A", "x", 1) != p.dedupe_key("A", "x", 2)
    assert p.sha256_hex(b"abc") == p.sha256_hex(b"abc")
