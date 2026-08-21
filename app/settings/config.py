from dotenv import load_dotenv
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

    database_url: str = "postgresql+asyncpg://platform:platform@db:5432/platform"

    cors_allowed_origins: list[str] = ["http://localhost:3000"]

    # JWT (access + refresh) -- stateless auth, least coupling to the Next.js
    # frontend since it doesn't need to relay session cookies cross-origin.
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 30

    # -- core/clients/asaas_client.py: mTLS client of asaas_payment, reusing
    # its scripts/generate_dev_certs.sh cert format. devbutter_platform never
    # holds the real ASAAS_API_KEY -- see docs/architecture/devbutter-platform-backend.md.
    asaas_payment_base_url: str = "https://asaas-nginx:8443"
    asaas_client_cert_path: str = "/app/certs/devbutter-platform.crt"
    asaas_client_key_path: str = "/app/certs/devbutter-platform.key"
    asaas_ca_cert_path: str = "/app/certs/ca.crt"
    asaas_request_timeout_seconds: float = 15.0

    # -- core/clients/butter_mind_client.py: plain HTTP, same Docker network.
    butter_mind_base_url: str = "http://mind-api:8000"
    butter_mind_request_timeout_seconds: float = 30.0

    # -- core/clients/relay_client.py: boto3 SQS publish into butter_relay's
    # shared queue. project_id identifies the "devbutter-platform" Project
    # registered in the relay admin (see notifications/service.py).
    aws_region: str = "us-east-1"
    relay_sqs_queue_url: str = ""
    relay_sqs_endpoint_url: str | None = None
    relay_project_id: str = ""

    # Asaas webhook receiver -- validates the `asaas-access-token` header.
    # See https://docs.asaas.com/docs/duvidas-frequentes-webhooks
    asaas_webhook_access_token: str = ""


settings = Settings()
