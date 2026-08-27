import pytest
from pydantic import ValidationError

from app.payment_methods.schemas import PaymentMethodSessionCreate


def test_session_contract_uses_camel_case_field_names() -> None:
    """contract-07 section 5.1 fixes the session request body as
    {purpose, supportedKids, invoiceId} -- literal camelCase JSON, not this
    backend's usual snake_case convention (see contract-07 section 0/2.2)."""

    payload = PaymentMethodSessionCreate.model_validate(
        {
            "purpose": "payment_method.create",
            "supportedKids": ["info-web-prod-2026-08"],
        }
    )
    assert payload.purpose == "payment_method.create"
    assert payload.supported_kids == ["info-web-prod-2026-08"]
    assert payload.invoice_id is None


def test_session_contract_rejects_purpose_outside_the_frozen_enum() -> None:
    with pytest.raises(ValidationError):
        PaymentMethodSessionCreate.model_validate(
            {"purpose": "pay_saved", "supportedKids": ["k1"]}
        )


def test_session_contract_requires_invoice_id_for_create_and_pay() -> None:
    with pytest.raises(ValidationError):
        PaymentMethodSessionCreate.model_validate(
            {"purpose": "payment_method.create_and_pay", "supportedKids": ["k1"]}
        )


def test_session_contract_forbids_invoice_id_for_create_only() -> None:
    with pytest.raises(ValidationError):
        PaymentMethodSessionCreate.model_validate(
            {
                "purpose": "payment_method.create",
                "supportedKids": ["k1"],
                "invoiceId": "00000000-0000-4000-8000-000000000001",
            }
        )


def test_session_contract_accepts_invoice_id_for_create_and_pay() -> None:
    payload = PaymentMethodSessionCreate.model_validate(
        {
            "purpose": "payment_method.create_and_pay",
            "supportedKids": ["k1"],
            "invoiceId": "00000000-0000-4000-8000-000000000001",
        }
    )
    assert str(payload.invoice_id) == "00000000-0000-4000-8000-000000000001"
