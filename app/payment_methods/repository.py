import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
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

    async def consume_context(
        self, context_id: str, request_id: str, now: datetime
    ) -> PaymentSecurityContext | None:
        statement = (
            update(PaymentSecurityContext)
            .where(
                PaymentSecurityContext.context_id == context_id,
                PaymentSecurityContext.state == "unused",
                PaymentSecurityContext.expires_at > now,
            )
            .values(state="consumed", request_id=request_id, consumed_at=now)
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
            raise ConflictError("Payment idempotency key already used for this invoice") from exc
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
