import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.nfse.models import NfseIssuance

_BACKOFF_MINUTES = (1, 5, 15, 60, 180)
_MAX_ATTEMPTS = 8


class NfseIssuanceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, issuance_id: uuid.UUID) -> NfseIssuance | None:
        return await self.session.get(NfseIssuance, issuance_id)

    async def get_by_invoice_id(self, invoice_id: uuid.UUID) -> NfseIssuance | None:
        result = await self.session.execute(
            select(NfseIssuance).where(NfseIssuance.invoice_id == invoice_id)
        )
        return result.scalar_one_or_none()

    async def get_by_asaas_nfse_id(self, asaas_nfse_id: str) -> NfseIssuance | None:
        result = await self.session.execute(
            select(NfseIssuance).where(NfseIssuance.asaas_nfse_id == asaas_nfse_id)
        )
        return result.scalar_one_or_none()

    async def get_by_payment_id(self, asaas_payment_id: str) -> NfseIssuance | None:
        result = await self.session.execute(
            select(NfseIssuance).where(NfseIssuance.asaas_payment_id == asaas_payment_id)
        )
        return result.scalar_one_or_none()

    async def create(self, record: NfseIssuance) -> NfseIssuance:
        self.session.add(record)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("NFS-e issuance already exists for this charge") from exc
        await self.session.refresh(record)
        return record

    async def save(self, record: NfseIssuance) -> NfseIssuance:
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def list_due(self, *, now: datetime, limit: int = 25) -> list[NfseIssuance]:
        result = await self.session.execute(
            select(NfseIssuance)
            .where(
                NfseIssuance.status.in_(("pending", "retry")),
                NfseIssuance.next_attempt_at <= now,
            )
            .order_by(NfseIssuance.next_attempt_at.asc())
            .limit(limit)
        )
        return list(result.scalars())

    @staticmethod
    def next_backoff(attempt_count: int) -> datetime:
        index = min(max(attempt_count - 1, 0), len(_BACKOFF_MINUTES) - 1)
        return datetime.now(UTC) + timedelta(minutes=_BACKOFF_MINUTES[index])

    @staticmethod
    def is_exhausted(attempt_count: int) -> bool:
        return attempt_count >= _MAX_ATTEMPTS
