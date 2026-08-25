from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.payment_methods.models import PaymentSecurityContext
from app.payment_methods.repository import PaymentMethodRepository


@pytest.mark.asyncio
async def test_context_consumption_is_single_use(db_session) -> None:
    repository = PaymentMethodRepository(db_session)
    context = await repository.create_context(
        PaymentSecurityContext(
            context_id="secctx_test_single_use_0001",
            account_id=uuid4(),
            company_id=uuid4(),
            invoice_id=uuid4(),
            operation="save_only",
            nonce=bytes(range(12)),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )

    first = await repository.consume_context(
        context.context_id,
        "00000000-0000-4000-8000-000000000001",
        datetime.now(UTC),
    )
    second = await repository.consume_context(
        context.context_id,
        "00000000-0000-4000-8000-000000000002",
        datetime.now(UTC),
    )

    assert first is not None
    assert first.state == "consumed"
    assert second is None
