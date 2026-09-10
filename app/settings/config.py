import os
from typing import Literal
from urllib.parse import quote

from dotenv import load_dotenv
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings parses .env into this Settings object only -- it never
# touches os.environ. boto3 (core/clients/relay_client.py) reads AWS
# credentials directly from the real process environment, so when running
# locally via `poetry run uvicorn` (not docker-compose's own `env_file:`,
# which does populate real container env vars) .env needs to be loaded here
# too. load_dotenv() never overrides a variable already set in the real
# environment, matching pydantic-settings' own precedence.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    debug: bool = False
    log_level: str = "INFO"
    environment: Literal["development", "staging", "preprod", "production"] = "development"

    database_host: str = "shared-postgres"
    database_port: int = 5432
    database_user: str = "platform"
    database_password: str = "platform"
    database_name: str = "platform"

    cors_allowed_origins: list[str] = ["http://localhost:3000"]

    # -- notifications/service.py: builds links (e.g. password_setup_url)
    # embedded in emails. No trailing slash.
    frontend_base_url: str = "http://localhost:3000"

    # JWT (access + refresh) -- stateless auth, least coupling to the Next.js
    # frontend since it doesn't need to relay session cookies cross-origin.
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 30

    # -- conversations/socket_handlers.py: the one place that reads the JWT
    # from a cookie instead of an Authorization header. Browser Socket.IO
    # clients can't set custom headers on the initial engineio handshake, so
    # the frontend relies on its existing httpOnly cookie (same name/value it
    # already sets for portal REST calls) sent via CORS credentials instead.
    # Must match devbutter_frontend's PORTAL_ACCESS_COOKIE (src/lib/portal/config.ts).
    portal_access_cookie_name: str = "db_portal_at"

    # -- core/clients/asaas_client.py: mTLS client of asaas_payment, reusing
    # its scripts/generate_dev_certs.sh cert format. devbutter_platform never
    # holds the real ASAAS_API_KEY -- see docs/architecture/devbutter-platform-backend.md.
    asaas_payment_base_url: str = "https://asaas-nginx:8443"
    asaas_client_cert_path: str = "/app/certs/devbutter-platform.crt"
    asaas_client_key_path: str = "/app/certs/devbutter-platform.key"
    asaas_ca_cert_path: str = "/app/certs/ca.crt"
    asaas_request_timeout_seconds: float = 15.0
    asaas_card_request_timeout_seconds: float = 70.0

    # Payment-method flow. `payment_payload_keyring` is a comma-separated
    # `kid:base64url-32-byte-key` keyring shared with the browser encryption
    # configuration. The browser key is transport-only; it is never auth.
    payment_payload_keyring: str = ""
    payment_token_encryption_key: str = ""
    payment_token_encryption_kid: str = "payment-token-v1"
    payment_context_ttl_seconds: int = 300

    # -- core/clients/butter_mind_client.py: plain HTTP, same Docker network.
    butter_mind_base_url: str = "http://mind-api:8000"
    butter_mind_request_timeout_seconds: float = 30.0
    butter_mind_internal_api_key: str = ""
    butter_mind_service_api_key: str = ""

    # -- core/clients/butter_blob_client.py: DevButter's blob store, same Docker
    # network (`blob-api`). Namespace-scoped: butter_blob binds every blob to
    # butter_blob_namespace and the service key authenticates the caller.
    butter_blob_base_url: str = "http://blob-api:8000"
    butter_blob_request_timeout_seconds: float = 30.0
    butter_blob_service_api_key: str = ""
    butter_blob_namespace: str = "devbutter_backend"

    # -- conversations: per-IP rate limit for the anonymous concierge funnel
    # (ADR-0004) so it can't be turned into an open LLM tap. 0 disables it.
    conversation_rate_limit_per_minute: int = 20

    # -- core/clients/relay_client.py: boto3 SQS publish into butter_relay's
    # shared queue. project_id identifies the "devbutter-platform" Project
    # registered in the relay admin (see notifications/service.py).
    aws_region: str = "us-east-1"
    relay_sqs_queue_url: str = ""
    relay_sqs_endpoint_url: str | None = None
    relay_project_id: str = ""

    # NFS-e (Layer A originator). Empty subaccount id means "do not emit".
    asaas_nfse_subaccount_id: str = ""
    asaas_nfse_municipal_service_id: str = ""
    asaas_nfse_municipal_service_code: str = ""
    asaas_nfse_municipal_service_name: str = ""
    asaas_nfse_service_description: str = "Serviços de desenvolvimento de software"
    butter_blob_api_key: str = ""
    nfse_blob_namespace: str = "nfse"
    butter_blob_timeout_seconds: float = 30.0
    nfse_email_logo_url: str = "https://devbutter.com/logo.png"

    # Asaas webhook receiver -- validates the `asaas-access-token` header.
    # See https://docs.asaas.com/docs/duvidas-frequentes-webhooks
    asaas_webhook_access_token: str = ""

    # Publicly reachable base URL of THIS backend, used to register the
    # subaccount's ACCOUNT_STATUS_* webhook on Asaas (webhooks[].url in the
    # POST /subaccounts body). No trailing slash. Provisioning refuses to run
    # with this empty so a subaccount is never created without a status
    # webhook wired back.
    backend_public_base_url: str = ""
    # Optional address put on webhooks[].email so Asaas notifies ops of
    # delivery problems; falls back to the account owner's email when unset.
    asaas_webhook_ops_email: str = ""

    @property
    def database_url(self) -> str:
        user = quote(self.database_user, safe="")
        password = quote(self.database_password, safe="")
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )

    @model_validator(mode="after")
    def reject_legacy_database_url_outside_dev(self) -> "Settings":
        if self.environment != "development" and os.environ.get("DATABASE_URL"):
            raise ValueError(
                "DATABASE_URL is no longer read; set DATABASE_HOST, DATABASE_PORT, "
                "DATABASE_USER, DATABASE_PASSWORD and DATABASE_NAME"
            )
        return self

    @property
    def payment_payload_keyring_dict(self) -> dict[str, str]:
        pairs = [item.strip() for item in self.payment_payload_keyring.split(",") if item.strip()]
        result: dict[str, str] = {}
        for pair in pairs:
            kid, separator, value = pair.partition(":")
            if not separator or not kid or not value or kid in result:
                raise ValueError("PAYMENT_PAYLOAD_KEYRING is invalid")
            result[kid] = value
        return result


settings = Settings()
