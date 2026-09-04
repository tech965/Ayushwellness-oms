from __future__ import annotations

from decimal import Decimal

from app.chat import formatting as fmt


def test_group_indian_lakh_crore_style() -> None:
    assert fmt.group_indian(0) == "0"
    assert fmt.group_indian(999) == "999"
    assert fmt.group_indian(1000) == "1,000"
    assert fmt.group_indian(123456) == "1,23,456"
    assert fmt.group_indian(384620) == "3,84,620"
    assert fmt.group_indian(12345678) == "1,23,45,678"


def test_rupees() -> None:
    assert fmt.rupees(384620) == "₹3,84,620"
    assert fmt.rupees(Decimal("384620.00")) == "₹3,84,620"
    assert fmt.rupees(Decimal("1298.50")) == "₹1,298.5"
    assert fmt.rupees(Decimal("1298.50"), paise=True) == "₹1,298.50"


def test_count() -> None:
    assert fmt.count(4281) == "4,281"
    assert fmt.count(Decimal("176")) == "176"


def test_percent() -> None:
    assert fmt.percent(176, 428) == "41.1%"
    assert fmt.percent(0, 0) == "0%"
    assert fmt.percent(252, 428) == "58.9%"


def test_ratio_percent() -> None:
    assert fmt.ratio_percent(41.13) == "41.1%"
    assert fmt.ratio_percent(Decimal("7")) == "7.0%"


def test_change_signed_and_undefined() -> None:
    assert fmt.change(428, 395) == "+8.4%"
    assert fmt.change(100, 125) == "-20.0%"
    assert fmt.change(5, 0) is None
