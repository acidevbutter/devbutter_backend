from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.payment_methods.models import PaymentSecurityContext
from app.payment_methods.repository import PaymentMethodRepository


def _context(**overrides) -> PaymentSecurityContext:
    defaults = dict(
        context_id="secctx_test_single_use_0001",
        account_id=uuid4(),
        company_id=uuid4(),
        purpose="payment_method.create",
        kid="info-web-test-01",
        nonce=bytes(range(12)),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    defaults.update(overrides)
    return PaymentSecurityContext(**defaults)


@pytest.mark.asyncio
async def test_context_transitions_unused_processing_consumed(db_session) -> None:
    repository = PaymentMethodRepository(db_session)
    context = await repository.create_context(_context())
    assert context.state == "unused"

    processing = await repository.begin_processing(context.context_id, datetime.now(UTC))
    assert processing is not None
    assert processing.state == "processing"
    assert processing.processing_started_at is not None

    consumed = await repository.finish_processing(
        context.context_id,
        request_id_used="00000000-0000-4000-8000-000000000001",
        failure_reason_code=None,
        now=datetime.now(UTC),
    )
    assert consumed is not None
    assert consumed.state == "consumed"
    assert consumed.request_id_used == "00000000-0000-4000-8000-000000000001"
    assert consumed.consumed_at is not None


@pytest.mark.asyncio
async def test_begin_processing_is_single_use(db_session) -> None:
    repository = PaymentMethodRepository(db_session)
    context = await repository.create_context(_context())

    first = await repository.begin_processing(context.context_id, datetime.now(UTC))
    second = await repository.begin_processing(context.context_id, datetime.now(UTC))

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_finish_processing_only_transitions_from_processing(db_session) -> None:
    repository = PaymentMethodRepository(db_session)
    context = await repository.create_context(_context())

    # Never entered "processing" -- finish_processing must not consume it.
    result = await repository.finish_processing(
        context.context_id,
        request_id_used="00000000-0000-4000-8000-000000000002",
        failure_reason_code=None,
        now=datetime.now(UTC),
    )
    assert result is None


@pytest.mark.asyncio
async def test_begin_processing_rejects_expired_context(db_session) -> None:
    repository = PaymentMethodRepository(db_session)
    context = await repository.create_context(
        _context(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )

    result = await repository.begin_processing(context.context_id, datetime.now(UTC))
    assert result is None


@pytest.mark.asyncio
async def test_nonce_is_unique_per_kid(db_session) -> None:
    from sqlalchemy.exc import IntegrityError

    repository = PaymentMethodRepository(db_session)
    nonce = bytes(range(12))
    await repository.create_context(_context(context_id="secctx_a", nonce=nonce, kid="kid-1"))

    with pytest.raises(IntegrityError):
        await repository.create_context(
            _context(context_id="secctx_b", nonce=nonce, kid="kid-1")
        )
