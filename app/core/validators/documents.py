"""CPF / CNPJ validation (check digits), used wherever a Brazilian tax id is
accepted -- account signup, the billing profile, and the Asaas subaccount
payload. Length alone is not enough: Asaas rejects a malformed ``cpfCnpj`` after
a wasted round trip, and a wrong digit in a merchant's CNPJ blocks their whole
onboarding, so we reject it at the edge.

All functions take the value with or without the usual mask
(``12.345.678/0001-95`` / ``123.456.789-09``) and operate on digits only.
"""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D")


def normalize_document(value: str) -> str:
    """Return only the digits of ``value`` (drops ``.`` ``/`` ``-`` and spaces)."""
    return _NON_DIGITS.sub("", value or "")


def _digits(value: str) -> list[int]:
    return [int(char) for char in value]


def is_valid_cpf(value: str) -> bool:
    """True when ``value`` is a CPF with valid check digits."""
    cpf = normalize_document(value)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    numbers = _digits(cpf)
    for position in (9, 10):
        weight = position + 1
        total = sum(numbers[i] * (weight - i) for i in range(position))
        check = (total * 10) % 11
        check = 0 if check == 10 else check
        if check != numbers[position]:
            return False
    return True


def is_valid_cnpj(value: str) -> bool:
    """True when ``value`` is a CNPJ with valid check digits."""
    cnpj = normalize_document(value)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False

    numbers = _digits(cnpj)
    first_weights = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    second_weights = [6, *first_weights]
    for weights, position in ((first_weights, 12), (second_weights, 13)):
        total = sum(numbers[i] * weights[i] for i in range(position))
        remainder = total % 11
        check = 0 if remainder < 2 else 11 - remainder
        if check != numbers[position]:
            return False
    return True


def classify_document(value: str) -> str | None:
    """``"CPF"`` / ``"CNPJ"`` when ``value`` is a valid one, else ``None``."""
    normalized = normalize_document(value)
    if len(normalized) == 11:
        return "CPF" if is_valid_cpf(normalized) else None
    if len(normalized) == 14:
        return "CNPJ" if is_valid_cnpj(normalized) else None
    return None


def is_valid_document(value: str) -> bool:
    """True when ``value`` is a valid CPF or a valid CNPJ."""
    return classify_document(value) is not None
