from __future__ import annotations

from contextlib import suppress
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.accounts.repository import AccountRepository
from app.core.clients.asaas_client import AsaasClient
from app.core.clients.blob_client import BlobClient
from app.core.clients.relay_client import RelayClient
from app.core.decorators import log_errors
from app.core.exceptions import ConflictError, UpstreamServiceError
from app.invoices.models import Invoice
from app.invoices.repository import InvoiceRepository
from app.nfse.models import NfseIssuance
from app.nfse.repository import NfseIssuanceRepository
from app.orders.repository import OrderRepository
from app.settings.config import settings
from app.webhooks.schemas import AsaasWebhookEvent

_TEMPLATE_NFSE_ISSUED = "nota_fiscal_emitida"
_PAID_STATUSES = {"PAID"}


def _safe_error(detail: str) -> str:
    return detail.replace("apiKey", "***").replace("access_token", "***")[:500]


@log_errors
class NfseService:
    """Originator NFS-e worker (Option B). asaas_payment stays outbound-only.

    Selo contract: persist the same outbox shape (external_reference unique per
    charge, asaas_nfse_id, status, blob ids) and call the same
    POST /subaccounts/{id}/invoices routes. Do not send INVOICE_* through the proxy.
    """

    def __init__(
        self,
        repository: NfseIssuanceRepository,
        invoice_repository: InvoiceRepository,
        order_repository: OrderRepository,
        account_repository: AccountRepository,
        asaas_client: AsaasClient,
        blob_client: BlobClient,
        relay_client: RelayClient,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.repository = repository
        self.invoice_repository = invoice_repository
        self.order_repository = order_repository
        self.account_repository = account_repository
        self.asaas_client = asaas_client
        self.blob_client = blob_client
        self.relay_client = relay_client
        self._http = http_client or httpx.AsyncClient(timeout=30.0)

    async def enqueue_from_paid_invoice(self, invoice: Invoice) -> NfseIssuance | None:
        if not invoice.requires_nfse or invoice.status not in _PAID_STATUSES:
            return None
        if not settings.asaas_nfse_subaccount_id:
            return None
        existing = await self.repository.get_by_invoice_id(invoice.id)
        if existing is not None:
            return existing
        record = NfseIssuance(
            invoice_id=invoice.id,
            asaas_account_id=settings.asaas_nfse_subaccount_id,
            asaas_payment_id=invoice.asaas_charge_id,
            external_reference=f"layer-a:{invoice.id}",
            status="pending",
            next_attempt_at=datetime.now(UTC),
        )
        try:
            return await self.repository.create(record)
        except ConflictError:
            return await self.repository.get_by_invoice_id(invoice.id)

    async def process_due(self, *, limit: int = 25) -> dict[str, int]:
        due = await self.repository.list_due(now=datetime.now(UTC), limit=limit)
        submitted = 0
        failed = 0
        for job in due:
            try:
                await self.process_job(job)
                submitted += 1
            except Exception:
                failed += 1
        return {"claimed": len(due), "ok": submitted, "failed": failed}

    async def process_job(self, job: NfseIssuance) -> NfseIssuance:
        job.attempt_count += 1
        if job.asaas_nfse_id:
            job.status = "submitted"
            return await self.repository.save(job)
        invoice = await self.invoice_repository.get(job.invoice_id)
        if invoice is None:
            return await self._fail(job, "Layer A invoice missing")
        payload = self._schedule_payload(invoice)
        try:
            body = await self.asaas_client.schedule_nfse(job.asaas_account_id, payload)
        except UpstreamServiceError as exc:
            return await self._fail(job, str(exc.detail))
        nfse_id = body.get("id")
        if not isinstance(nfse_id, str) or not nfse_id:
            return await self._fail(job, "asaas_payment returned no NFS-e id")
        job.asaas_nfse_id = nfse_id
        job.status = "submitted"
        job.last_error = None
        return await self.repository.save(job)

    def _schedule_payload(self, invoice: Invoice) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "payment": invoice.asaas_charge_id,
            "serviceDescription": settings.asaas_nfse_service_description,
            "value": float(invoice.amount),
            "effectiveDate": date.today().isoformat(),
        }
        if settings.asaas_nfse_municipal_service_id:
            payload["municipalServiceId"] = settings.asaas_nfse_municipal_service_id
        elif settings.asaas_nfse_municipal_service_code:
            payload["municipalServiceCode"] = settings.asaas_nfse_municipal_service_code
        if settings.asaas_nfse_municipal_service_name:
            payload["municipalServiceName"] = settings.asaas_nfse_municipal_service_name
        return payload

    async def apply_invoice_webhook(self, event: AsaasWebhookEvent) -> None:
        invoice = event.invoice
        if invoice is None:
            return
        job = await self.repository.get_by_asaas_nfse_id(invoice.id)
        if job is None and invoice.payment:
            job = await self.repository.get_by_payment_id(invoice.payment)
        if job is None:
            return
        status_map = {
            "INVOICE_CREATED": "submitted",
            "INVOICE_SYNCHRONIZED": "submitted",
            "INVOICE_AUTHORIZED": "authorized",
            "INVOICE_ERROR": "retry",
            "INVOICE_CANCELED": "canceled",
            "INVOICE_CANCELLATION_DENIED": "authorized",
            "INVOICE_PROCESSING_CANCELLATION": "submitted",
        }
        job.status = status_map.get(event.event, job.status)
        if invoice.number:
            job.nfse_number = invoice.number
        if invoice.validationCode:
            job.validation_code = invoice.validationCode
        if event.event == "INVOICE_ERROR":
            job.last_error = _safe_error(invoice.status or "INVOICE_ERROR")
            job.next_attempt_at = self.repository.next_backoff(job.attempt_count)
        await self.repository.save(job)
        if event.event == "INVOICE_AUTHORIZED":
            await self.host_authorized_document(job)

    async def host_authorized_document(self, job: NfseIssuance) -> None:
        if job.pdf_blob_id:
            return
        if not job.asaas_nfse_id:
            await self._fail(job, "authorized webhook without asaas_nfse_id")
            return
        try:
            body = await self.asaas_client.get_nfse(job.asaas_account_id, job.asaas_nfse_id)
        except UpstreamServiceError as exc:
            await self._fail(job, str(exc.detail))
            return
        job.nfse_number = str(body.get("number") or job.nfse_number or "")
        job.validation_code = body.get("validationCode") or job.validation_code
        pdf_url = body.get("pdfUrl")
        xml_url = body.get("xmlUrl")
        metadata = {
            "nfse_id": job.asaas_nfse_id,
            "origin": "devbutter_backend",
            "content_role": "nfse_pdf",
        }
        if isinstance(pdf_url, str) and pdf_url:
            pdf_bytes = await self._download_document(pdf_url)
            uploaded = await self.blob_client.upload(
                filename=f"nfse-{job.asaas_nfse_id}.pdf",
                content=pdf_bytes,
                content_type="application/pdf",
                visibility="public",
                metadata=metadata,
            )
            job.pdf_blob_id = str(uploaded.get("id") or "")
            public_url = uploaded.get("public_url")
            if isinstance(public_url, str):
                job.document_url = public_url
        if isinstance(xml_url, str) and xml_url:
            xml_bytes = await self._download_document(xml_url)
            uploaded_xml = await self.blob_client.upload(
                filename=f"nfse-{job.asaas_nfse_id}.xml",
                content=xml_bytes,
                content_type="application/xml",
                visibility="private",
                metadata={**metadata, "content_role": "nfse_xml"},
            )
            job.xml_blob_id = str(uploaded_xml.get("id") or "")
        job.status = "authorized"
        job.last_error = None
        await self.repository.save(job)
        with suppress(UpstreamServiceError):
            await self.asaas_client.ingest_mirror_invoice(
                job.asaas_account_id,
                {
                    "id": job.asaas_nfse_id,
                    "status": "AUTHORIZED",
                    "number": job.nfse_number,
                    "payment": job.asaas_payment_id,
                },
            )
        await self._notify_issued(job)

    async def _download_document(self, url: str) -> bytes:
        response = await self._http.get(url)
        if response.status_code >= 400:
            raise UpstreamServiceError("failed to fetch NFS-e document bytes")
        content_type = response.headers.get("content-type", "")
        if "json" in content_type.lower() and not response.content.startswith(b"%PDF"):
            raise UpstreamServiceError("unexpected document MIME from provider")
        return response.content

    async def _notify_issued(self, job: NfseIssuance) -> None:
        invoice = await self.invoice_repository.get(job.invoice_id)
        if invoice is None:
            return
        order = await self.order_repository.get(invoice.order_id)
        if order is None:
            return
        account = await self.account_repository.get(order.account_id)
        if account is None:
            return
        company = await self.account_repository.get_company(account.company_id)
        if company is None:
            return
        user = await self.account_repository.get_user(company.user_id)
        if user is None:
            return
        self.relay_client.send(
            _TEMPLATE_NFSE_ISSUED,
            user.email,
            {
                "name": user.name,
                "title": "Sua nota fiscal foi emitida",
                "message": "A nota fiscal do serviço contratado já está disponível.",
                "preheader": "Nota fiscal emitida",
                "cta_text": "Abrir nota fiscal",
                "logo_url": settings.nfse_email_logo_url,
                "blob_id": job.pdf_blob_id,
                "nfse_id": job.asaas_nfse_id,
                "nfse_number": job.nfse_number,
                "project_name": "DevButter",
            },
        )

    async def _fail(self, job: NfseIssuance, detail: str) -> NfseIssuance:
        job.last_error = _safe_error(detail)
        if self.repository.is_exhausted(job.attempt_count):
            job.status = "exhausted"
        else:
            job.status = "retry"
            job.next_attempt_at = self.repository.next_backoff(job.attempt_count)
        return await self.repository.save(job)
