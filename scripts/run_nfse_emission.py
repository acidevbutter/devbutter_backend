"""One-shot NFS-e emission worker.

Run from ECS scheduled task / cron, never in-process on the web app:

    poetry run python scripts/run_nfse_emission.py

Cadence: every 1–5 minutes. Idempotent: unique external_reference per Layer A
charge; POST to asaas_payment is skipped when asaas_nfse_id is already stored.
Exit 1 if any claimed job remains failed/exhausted this run.
There is no SQS DLQ in this service — exhausted rows are the observable sink.
"""

from __future__ import annotations

import asyncio
import sys

from app.accounts.repository import AccountRepository
from app.core.clients.asaas_client import close_asaas_client, get_asaas_client
from app.core.clients.blob_client import get_blob_client
from app.core.clients.relay_client import get_relay_client
from app.db.session import session_factory
from app.invoices.repository import InvoiceRepository
from app.nfse.repository import NfseIssuanceRepository
from app.nfse.service import NfseService
from app.orders.repository import OrderRepository


async def main() -> int:
    asaas_client = get_asaas_client()
    blob_client = get_blob_client()
    relay_client = get_relay_client()
    try:
        async with session_factory() as session:
            service = NfseService(
                NfseIssuanceRepository(session),
                InvoiceRepository(session),
                OrderRepository(session),
                AccountRepository(session),
                asaas_client,
                blob_client,
                relay_client,
            )
            result = await service.process_due()
        if result["failed"]:
            return 1
        return 0
    finally:
        await close_asaas_client()
        await blob_client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
