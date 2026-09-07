from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NfseIssuanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    invoice_id: UUID
    asaas_account_id: str
    asaas_payment_id: str
    asaas_nfse_id: str | None
    status: str
    nfse_number: str | None
    pdf_blob_id: str | None
    last_error: str | None
    attempt_count: int
    next_attempt_at: datetime
