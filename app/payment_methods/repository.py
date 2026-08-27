import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IdempotencyConflictError
from app.payment_methods.models import PaymentAttempt, PaymentMethod, PaymentSecurityContext


class PaymentMethodRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_context(self, context: PaymentSecurityContext) -> PaymentSecurityContext:
        self.session.add(context)
        await self.session.commit()
        await self.session.refresh(context)
        return context

    async def get_context(self, context_id: str) -> PaymentSecurityContext | None:
        result = await self.session.execute(
            select(PaymentSecurityContext).where(PaymentSecurityContext.context_id == context_id)
        )
        return result.scalar_one_or_none()

    async def begin_processing(
        self, context_id: str, now: datetime
    ) -> PaymentSecurityContext | None:
        """Atomic `unused -> processing` transition (contract-07 section 4, rule 1).

        A single conditional UPDATE, gated on `state == 'unused'` and
        `expires_at > now`. If it affects zero rows -- context already
        consumed/processing/expired, or lost the race against a concurrent
        request for the same `context_id` -- the caller gets `None` and must
        surface the generic `invalid_secure_payload` error without
        distinguishing why. Exactly one of two simultaneous callers can ever
        get a non-`None` result back, because the UPDATE...WHERE takes the
        row lock and commits atomically.
        """

        statement = (
            update(PaymentSecurityContext)
            .where(
                PaymentSecurityContext.context_id == context_id,
                PaymentSecurityContext.state == "unused",
                PaymentSecurityContext.expires_at > now,
            )
            .values(state="processing", processing_started_at=now)
            .returning(PaymentSecurityContext)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(statement)
        context = result.scalar_one_or_none()
        await self.session.commit()
        if context is not None:
            await self.session.refresh(context)
        return context

    async def finish_processing(
        self,
        context_id: str,
        *,
        request_id_used: str | None,
        failure_reason_code: str | None,
        now: datetime,
    ) -> PaymentSecurityContext | None:
        """`processing -> consumed` (contract-07 section 4, rule 2).

        Unconditional on outcome: called whether decryption/authentication
        succeeded or failed, and there is no path back to `unused`. Returns
        `None` only if the row somehow left `processing` already (should not
        happen, since only the holder of the earlier `begin_processing` call
        reaches this method) -- the caller must treat that defensively as a
        failure too.

        `request_id_used` is protected by `UNIQUE(account_id,
        request_id_used)`; a genuine collision (the same subject reusing an
        envelope `requestId` across two different contexts) raises
        `IntegrityError` here, which the caller must catch and retry with
        `request_id_used=None` so the row still reaches `consumed` -- a
        context must never be stuck in `processing` forever.
        """

        statement = (
            update(PaymentSecurityContext)
            .where(
                PaymentSecurityContext.context_id == context_id,
                PaymentSecurityContext.state == "processing",
            )
            .values(
                state="consumed",
                request_id_used=request_id_used,
                failure_reason_code=failure_reason_code,
                consumed_at=now,
            )
            .returning(PaymentSecurityContext)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(statement)
        context = result.scalar_one_or_none()
        await self.session.commit()
        if context is not None:
            await self.session.refresh(context)
        return context

    async def create_method(self, method: PaymentMethod) -> PaymentMethod:
        self.session.add(method)
        await self.session.commit()
        await self.session.refresh(method)
        return method

    async def list_methods(self, company_id: uuid.UUID) -> list[PaymentMethod]:
        result = await self.session.execute(
            select(PaymentMethod)
            .where(PaymentMethod.company_id == company_id, PaymentMethod.status == "active")
            .order_by(PaymentMethod.created_at.desc())
        )
        return list(result.scalars())

    async def get_method(self, payment_method_id: uuid.UUID) -> PaymentMethod | None:
        return await self.session.get(PaymentMethod, payment_method_id)

    async def revoke_method(self, method: PaymentMethod, now: datetime) -> PaymentMethod:
        method.status = "revoked"
        method.revoked_at = now
        await self.session.commit()
        await self.session.refresh(method)
        return method

    async def create_attempt(self, attempt: PaymentAttempt) -> PaymentAttempt:
        self.session.add(attempt)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            # UNIQUE(invoice_id, idempotency_key) violated -- contract-07
            # section 5.3: `idempotency_conflict` (409). Deliberately no
            # free-text detail.
            raise IdempotencyConflictError() from exc
        await self.session.refresh(attempt)
        return attempt

    async def set_attempt_state(
        self,
        attempt: PaymentAttempt,
        state: str,
        *,
        provider_payment_id: str | None = None,
        failure_code: str | None = None,
    ) -> PaymentAttempt:
        attempt.state = state
        if provider_payment_id is not None:
            attempt.provider_payment_id = provider_payment_id
        attempt.failure_code = failure_code
        await self.session.commit()
        await self.session.refresh(attempt)
        return attempt

    async def list_unresolved_attempts(self) -> list[PaymentAttempt]:
        result = await self.session.execute(
            select(PaymentAttempt).where(PaymentAttempt.state == "unknown")
        )
        return list(result.scalars())

    async def list_attempts_by_invoice(self, invoice_id: uuid.UUID) -> list[PaymentAttempt]:
        """Used by the Asaas webhook handler to reconcile `PaymentAttempt`
        rows for a saved-card charge asynchronously confirmed by Asaas --
        the synchronous `pay()` response can be `submitted`/`unknown` before
        the webhook arrives with the final status.
        """

        result = await self.session.execute(
            select(PaymentAttempt).where(PaymentAttempt.invoice_id == invoice_id)
        )
        return list(result.scalars())
