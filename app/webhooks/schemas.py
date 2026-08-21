from pydantic import BaseModel


class AsaasWebhookPayment(BaseModel):
    id: str
    status: str


class AsaasWebhookEvent(BaseModel):
    """Asaas's webhook body: {"event": "PAYMENT_RECEIVED", "payment": {...}}.
    Only the fields this service actually consumes are typed; everything else
    in the payload is ignored. See https://docs.asaas.com/docs/duvidas-frequentes-webhooks.
    """

    event: str
    payment: AsaasWebhookPayment
