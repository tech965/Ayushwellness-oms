from __future__ import annotations

import pytest
from app.services.whatsapp_contact_service import (
    InvalidPhoneNumberError,
    create_contact,
    normalize_phone,
)
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


def test_normalize_bare_10_digit_indian_number():
    assert normalize_phone("9991234567") == "919991234567"


def test_normalize_already_has_country_code():
    assert normalize_phone("+91 9991 234 567") == "919991234567"


def test_normalize_never_double_prefixes():
    assert normalize_phone("919991234567") == "919991234567"


def test_normalize_rejects_too_short():
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone("12345")


async def test_create_contact_is_idempotent_by_phone(db_session: AsyncSession):
    c1 = await create_contact(db_session, name="Alice", phone="9991234567")
    await db_session.commit()
    c2 = await create_contact(db_session, name="Alice Again", phone="+91 9991234567")
    await db_session.commit()
    assert c1.id == c2.id
    assert c1.phone_number == "919991234567"
