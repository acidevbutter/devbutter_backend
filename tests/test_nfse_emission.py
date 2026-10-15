from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import cast
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account, Company, User
from app.core.clients.relay_client import RelayClient
from app.invoices.models import Invoice
from app.nfse.models import NfseIssuance
from app.nfse.repository import NfseIssuanceRepository
from app.nfse.service import NfseService
from app.orders.models import Order
from app.settings.config import settings
from app.webhooks.schemas import AsaasWebhookEvent, AsaasWebhookInvoice


class _FakeAsaas:
    def __init__(self) -> None:
        self.scheduled: list[dict] = []
        self.fetched: list[str] = []
        self.mirrored: list[dict] = []

    async def schedule_nfse(self, account_id: str, payload: dict) -> dict:
        self.scheduled.append({"account_id": account_id, **payload})
        return {"id": "inv_nfse_1", "status": "SCHEDULED"}

    async def get_nfse(self, account_id: str, nfse_id: str) -> dict:
        self.fetched.append(nfse_id)
        return {
            "id": nfse_id,
            "status": "AUTHORIZED",
            "number": "123",
            "validationCode": "ABC",
            "pdfUrl": "https://files.example.test/nfse.pdf",
            "xmlUrl": None,
            "payment": "pay_nfse_1",
        }

    async def ingest_mirror_invoice(self, account_id: str, resource: dict) -> dict:
        self.mirrored.append(resource)
        return {"id": resource["id"], "created": True}


class _FakeBlob:
    def __init__(self) -> None:
        self.uploads: list[dict] = []

    async def upload(self, **kwargs) -> dict:
        self.uploads.append(kwargs)
        return {
            "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "public_url": "https://blob.test/v1/public/nfse/nota.pdf",
            "visibility": kwargs.get("visibility"),
        }


class _FakeRelay:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send(
        self, template_key: str, to: str, payload: dict, project_id=None, queue=None
    ) -> None:
        self.sent.append((template_key, to, payload))


class _FakeHttp:
    async def get(self, url: str):
        class _Resp:
            status_code = 200
            content = b"%PDF-1.4 fake"
            headers = {"content-type": "application/pdf"}

        return _Resp()


async def _make_paid_invoice(db_session: AsyncSession, *, requires_nfse: bool) -> Invoice:
    user = User(email=f"{uuid4().hex[:8]}@example.com", password_hash="x", name="Cliente")
    db_session.add(user)
    await db_session.flush()
    company = Company(user_id=user.id, name="Empresa NF", cnpj="12345678000199")
    db_session.add(company)
    await db_session.flush()
    account = Account(company_id=company.id, product_name="Layer A")
    db_session.add(account)
    await db_session.flush()
    order = Order(quote_id=uuid4(), account_id=account.id)
    db_session.add(order)
    await db_session.flush()
    invoice = Invoice(
        order_id=order.id,
        asaas_invoice_id="pay_nfse_1",
        amount=Decimal("100.00"),
        currency="BRL",
        status="PAID",
        requires_nfse=requires_nfse,
        due_date=date(2026, 9, 10),
    )
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)
    return invoice


def _service(
    db_session: AsyncSession, asaas: _FakeAsaas, blob: _FakeBlob, relay: _FakeRelay
) -> NfseService:
    from app.accounts.repository import AccountRepository
    from app.invoices.repository import InvoiceRepository
    from app.orders.repository import OrderRepository

    return NfseService(
        NfseIssuanceRepository(db_session),
        InvoiceRepository(db_session),
        OrderRepository(db_session),
        AccountRepository(db_session),
        asaas,  # type: ignore[arg-type]
        blob,  # type: ignore[arg-type]
        relay,  # type: ignore[arg-type]
        http_client=_FakeHttp(),  # type: ignore[arg-type]
    )


async def test_enqueue_skips_when_requer_nota_false(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_nfse_subaccount_id", "acc_fiscal")
    invoice = await _make_paid_invoice(db_session, requires_nfse=False)
    service = _service(db_session, _FakeAsaas(), _FakeBlob(), _FakeRelay())
    assert await service.enqueue_from_paid_invoice(invoice) is None


async def test_worker_is_idempotent_on_second_post(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_nfse_subaccount_id", "acc_fiscal")
    monkeypatch.setattr(settings, "asaas_nfse_municipal_service_id", "203561")
    invoice = await _make_paid_invoice(db_session, requires_nfse=True)
    asaas = _FakeAsaas()
    service = _service(db_session, asaas, _FakeBlob(), _FakeRelay())
    job = await service.enqueue_from_paid_invoice(invoice)
    assert job is not None
    await service.process_job(job)
    again = await service.enqueue_from_paid_invoice(invoice)
    assert again is not None
    await service.process_job(again)
    assert len(asaas.scheduled) == 1
    row = await db_session.get(NfseIssuance, job.id)
    assert row is not None
    assert row.asaas_nfse_id == "inv_nfse_1"
    assert row.status == "submitted"


async def test_invoice_authorized_hosts_blob_and_sends_relay_without_private_url(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_nfse_subaccount_id", "acc_fiscal")
    monkeypatch.setattr(settings, "asaas_nfse_municipal_service_id", "203561")
    invoice = await _make_paid_invoice(db_session, requires_nfse=True)
    asaas = _FakeAsaas()
    blob = _FakeBlob()
    relay = _FakeRelay()
    service = _service(db_session, asaas, blob, relay)
    job = await service.enqueue_from_paid_invoice(invoice)
    assert job is not None
    await service.process_job(job)
    event = AsaasWebhookEvent(
        event="INVOICE_AUTHORIZED",
        invoice=AsaasWebhookInvoice(id="inv_nfse_1", status="AUTHORIZED", number="123"),
    )
    await service.apply_invoice_webhook(event)
    assert blob.uploads[0]["visibility"] == "public"
    assert blob.uploads[0]["content_type"] == "application/pdf"
    assert relay.sent[0][0] == "nota_fiscal_emitida"
    payload = relay.sent[0][2]
    assert payload["blob_id"]
    assert "pdfUrl" not in payload
    assert "https://files.example.test" not in str(payload)


async def test_invoice_webhook_duplicate_event_does_not_reupload(
    db_session: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "asaas_nfse_subaccount_id", "acc_fiscal")
    invoice = await _make_paid_invoice(db_session, requires_nfse=True)
    asaas = _FakeAsaas()
    blob = _FakeBlob()
    relay = _FakeRelay()
    nfse = _service(db_session, asaas, blob, relay)
    job = await nfse.enqueue_from_paid_invoice(invoice)
    assert job is not None
    await nfse.process_job(job)

    from app.accounts.repository import AccountRepository
    from app.invoices.repository import InvoiceRepository
    from app.notifications.service import NotificationService
    from app.orders.repository import OrderRepository
    from app.webhooks.repository import WebhookEventRepository
    from app.webhooks.service import WebhookService

    webhooks = WebhookService(
        InvoiceRepository(db_session),
        OrderRepository(db_session),
        AccountRepository(db_session),
        NotificationService(cast(RelayClient, relay)),
        WebhookEventRepository(db_session),
        nfse_service=nfse,
    )
    payload = {
        "id": "evt_nfse_dup",
        "event": "INVOICE_AUTHORIZED",
        "invoice": {
            "id": "inv_nfse_1",
            "status": "AUTHORIZED",
            "number": "123",
            "pdfUrl": "https://files.example.test/secret.pdf",
        },
    }
    event = AsaasWebhookEvent.model_validate(payload)
    first = await webhooks.process_event(event, payload)
    second = await webhooks.process_event(event, payload)
    assert first.id == second.id
    assert len(blob.uploads) == 1
    assert first.payload["invoice"]["pdfUrl"] == "[redacted]"
