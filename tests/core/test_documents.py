import pytest

from app.core.validators.documents import (
    classify_document,
    is_valid_cnpj,
    is_valid_cpf,
    is_valid_document,
    normalize_document,
)

VALID_CPFS = ["529.982.247-25", "52998224725", "111.444.777-35"]
INVALID_CPFS = ["529.982.247-24", "11111111111", "123", "", "1234567890a"]

# Check digits computed with the same algorithm; masked and unmasked forms.
VALID_CNPJS = ["45.285.573/0001-47", "00.000.000/0001-91", "11.222.333/0001-81"]
INVALID_CNPJS = ["45.285.573/0001-01", "00000000000000", "11.222.333/0001-82", "abc"]


@pytest.mark.parametrize("value", VALID_CPFS)
def test_valid_cpf(value: str) -> None:
    assert is_valid_cpf(value) is True


@pytest.mark.parametrize("value", INVALID_CPFS)
def test_invalid_cpf(value: str) -> None:
    assert is_valid_cpf(value) is False


@pytest.mark.parametrize("value", VALID_CNPJS)
def test_valid_cnpj(value: str) -> None:
    assert is_valid_cnpj(value) is True


@pytest.mark.parametrize("value", INVALID_CNPJS)
def test_invalid_cnpj(value: str) -> None:
    assert is_valid_cnpj(value) is False


def test_normalize_document_strips_mask() -> None:
    assert normalize_document("45.285.573/0001-00") == "45285573000100"
    assert normalize_document(" 529.982.247-25 ") == "52998224725"


def test_classify_document() -> None:
    assert classify_document("529.982.247-25") == "CPF"
    assert classify_document("45.285.573/0001-47") == "CNPJ"
    assert classify_document("529.982.247-24") is None
    assert classify_document("123") is None


def test_is_valid_document() -> None:
    assert is_valid_document("52998224725") is True
    assert is_valid_document("45285573000147") is True
    assert is_valid_document("00000000000") is False
