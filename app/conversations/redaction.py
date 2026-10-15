from typing import Any

# The AI's price guesses must not reach the visitor before an admin approves the
# quote (docs/plano-preco-so-apos-aprovacao.md). butter_mind's preview carries
# them (`budget_range`, `options[].price_range`); the stored copy stays intact for
# the admin review screens, only client-facing responses are redacted.
_HIDDEN_KEYS = frozenset({"price_range", "budget_range"})


def redact_preview(value: Any) -> Any:
    """Returns a copy of `value` without any `price_range`/`budget_range` key, at any depth."""
    if isinstance(value, dict):
        return {k: redact_preview(v) for k, v in value.items() if k not in _HIDDEN_KEYS}
    if isinstance(value, list):
        return [redact_preview(item) for item in value]
    return value
