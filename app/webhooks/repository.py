import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.webhooks.models import WebhookEvent


class WebhookEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        *,
        event_type: str,
        related_invoice_id: uuid.UUID | None,
        payload: dict[str, Any],
        event_id: str | None = None,
    ) -> WebhookEvent:
        dialect = self.session.get_bind().dialect.name
        statement_factory = (
            postgresql_insert if dialect == "postgresql" else sqlite_insert
        )
        statement = (
            statement_factory(WebhookEvent)
            .values(
                event_type=event_type,
                related_invoice_id=related_invoice_id,
                payload=payload,
                event_id=event_id,
            )
            .on_conflict_do_nothing(index_elements=[WebhookEvent.event_id])
            .returning(WebhookEvent)
        )
        event = (await self.session.execute(statement)).scalar_one_or_none()
        await self.session.commit()
        if event is None:
            # The insert lost the UNIQUE(event_id) race. No rollback is
            # needed, which keeps independent request sessions isolated even
            # when SQLite tests share one connection.
            raise ConflictError(f"Webhook event {event_id!r} already recorded")
        return event

    async def get(self, event_id: uuid.UUID) -> WebhookEvent | None:
        return await self.session.get(WebhookEvent, event_id)

    async def get_by_event_id(self, event_id: str) -> WebhookEvent | None:
        """Idempotency lookup by the emitter-assigned event_id (not this
        table's own primary key) -- see WebhookEvent.event_id docstring.
        """
        result = await self.session.execute(
            select(WebhookEvent).where(WebhookEvent.event_id == event_id)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        event_type: str | None,
        has_error: bool | None,
        offset: int,
        limit: int,
    ) -> tuple[list[WebhookEvent], int, int]:
        query = select(WebhookEvent)
        count_query = select(func.count()).select_from(WebhookEvent)
        error_count_query = select(func.count()).select_from(WebhookEvent).where(
            WebhookEvent.processed_ok.is_(False)
        )
        conditions = []
        if event_type:
            conditions.append(WebhookEvent.event_type == event_type)
        if has_error is True:
            conditions.append(WebhookEvent.processed_ok.is_(False))
        elif has_error is False:
            conditions.append(WebhookEvent.processed_ok.is_(True))
        if conditions:
            query = query.where(*conditions)
            count_query = count_query.where(*conditions)
            if event_type:
                error_count_query = error_count_query.where(WebhookEvent.event_type == event_type)

        total = int((await self.session.execute(count_query)).scalar_one())
        error_count = int((await self.session.execute(error_count_query)).scalar_one())
        result = await self.session.execute(
            query.order_by(WebhookEvent.received_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars()), total, error_count

    async def mark_processed(self, event: WebhookEvent) -> WebhookEvent:
        event.processed_ok = True
        event.error_message = None
        event.processed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(event)
        return event

    async def mark_failed(self, event: WebhookEvent, error_message: str) -> WebhookEvent:
        event.processed_ok = False
        event.error_message = error_message[:2000]
        event.processed_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(event)
        return event
