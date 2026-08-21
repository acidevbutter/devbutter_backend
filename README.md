# devbutter_platform

The orchestrator backend for the DevButter platform (5th service alongside `asaas_payment`,
`butter_mind`, `butter_relay`, and `devbutter_frontend`). It implements the flow described in
[`docs/architecture/architecture-ai-quote-client-portal.md`](../docs/architecture/architecture-ai-quote-client-portal.md)
and [`docs/architecture/devbutter-platform-backend.md`](../docs/architecture/devbutter-platform-backend.md):
a visitor chats with an AI concierge (or Marcos sets a quote manually), the draft goes through an
admin review queue, and an approved quote becomes an Order billed through Asaas.

This service owns no billing, chat, or email logic of its own — it holds the business state
(account, quote, order, invoice, catalog) and calls the three existing services as HTTP/mTLS/SQS
clients. See `app/core/clients/`.

```
Client app (devbutter_frontend)
        |
        v
devbutter_platform (this service) -- Postgres (accounts, conversations, quotes, orders, invoices, catalog)
        |            |             |
        | mTLS        | HTTP        | boto3 SQS
        v            v             v
  asaas_payment   butter_mind   butter_relay
```

## Modules

Each domain follows the same five-file shape (`router.py`, `schemas.py`, `models.py`, `service.py`,
`repository.py`); `webhooks/`, `admin/`, and `notifications/` deviate deliberately because they hold
no entity of their own (see `docs/architecture/diagrams/backend-structure.mmd`):

| Module | Responsibility |
|---|---|
| `accounts` | signup/login (JWT access+refresh), profile, the `AsaasCustomerLink` |
| `conversations` | wraps a `butter_mind` guided-diagnosis session; local transcript mirror |
| `quotes` | draft (`source`: `ai` \| `manual`), status (`draft`/`approved`/`edited`/`rejected`) |
| `orders` | created on quote approval; drives Asaas Customer + Invoice creation |
| `invoices` | local mirror of an Asaas cobrança, kept in sync via webhook |
| `webhooks` | public receiver for Asaas payment-status events (`asaas-access-token`, idempotent) |
| `service_catalog` | metadata a client registers about their own business/store |
| `admin` | Marcos's review queue — a status view over `quotes`, not its own entity |
| `notifications` | builds the payload and publishes it onto `butter_relay`'s SQS queue |
| `core/clients` | the three integration clients — the only place this service talks to a sibling |

## Integration contracts

- **→ `asaas_payment`** (`core/clients/asaas_client.py`): mTLS client, reusing
  `asaas_payment/scripts/generate_dev_certs.sh`. Calls the generic pass-through routes
  (`POST /customers`, `POST /payments`, forwarded to Asaas's own `/v3/customers` and `/v3/payments`)
  because Layer A bills DevButter's own clients under DevButter's single Asaas account — it does not
  act through a white-label subaccount (Layer B, out of scope here; see the architecture doc §2).
  This service never sees `ASAAS_API_KEY`.
- **→ `butter_mind`** (`core/clients/butter_mind_client.py`): plain HTTP over the shared Docker
  network (`mind-api`), driving butter_mind's `/diagnosis/sessions` flow. butter_mind produces the
  schema-validated draft (`problem_summary`, `services_of_interest`, `budget_range`, `timeline`);
  `quotes/service.py` turns that into a `Quote(source="ai")`. butter_mind has no write access to
  Quote/Order/Invoice.
- **→ `butter_relay`** (`core/clients/relay_client.py`): `boto3` `SendMessage` onto the shared SQS
  queue, tagged with this service's `project_id`. Only builds `{template_key, to, payload}` —
  templates (`devbutter_quote_approved`, `devbutter_payment_status_changed`) must be registered in
  the `butter_relay` admin, not hardcoded here.
- **← Asaas webhook** (`webhooks/router.py`): `POST /webhooks/asaas`, validates the
  `asaas-access-token` header. Idempotent by construction — `InvoiceRepository.update_status()` is a
  no-op if the mapped status is already applied.

### Known gap to close before this runs end-to-end

`ASAAS_PAYMENT_BASE_URL` must reach `asaas_payment`'s **nginx** (the mTLS termination point), not its
`app` container directly. As shipped, `asaas_payment/docker-compose.yml`'s `nginx` service is only on
the `edge`/`internal` networks — it needs to also join `devbutter_shared` (with an alias, e.g.
`asaas-nginx`) for this service to reach it over mTLS the way the architecture doc intends. That's a
one-line addition to a sibling repo's compose file, left for Marcos to make deliberately rather than
changed silently here. Until then, local end-to-end testing of the order-approval flow needs that
network wired up (or `ASAAS_PAYMENT_BASE_URL` pointed at a locally-reachable nginx some other way).

## Local development

```bash
poetry install
cp .env.example .env
# Fill in JWT_SECRET, ASAAS_WEBHOOK_ACCESS_TOKEN, RELAY_PROJECT_ID.
# mTLS client cert for asaas_payment:
#   ../asaas_payment/scripts/generate_dev_certs.sh devbutter-platform
#   cp ../asaas_payment/certs/{devbutter-platform.crt,devbutter-platform.key,ca.crt} ./certs/
#   (then add "devbutter-platform" to asaas_payment's ALLOWED_CLIENT_CNS)
docker compose up --build
```

Requires the `devbutter_shared` external Docker network the other services already join:

```bash
docker network create devbutter_shared  # if it doesn't exist yet
```

Run migrations:

```bash
poetry run alembic upgrade head
```

Generate a new migration after changing a model (review it before applying — autogenerate can miss
renames):

```bash
poetry run alembic revision --autogenerate -m "..."
```

## Testing

```bash
poetry run pytest
poetry run ruff check .
```

Covers: signup/login/refresh and the email-uniqueness 409, service-catalog CRUD with ownership
checks, the `log_call`/`log_errors` decorators (sync, async, error path), and the full
manual-quote → admin-approval → Order/Asaas-Customer/Invoice orchestration with `asaas_payment` and
`butter_relay` faked at the client boundary — including the 409 on re-approving an already-approved
quote and the Asaas webhook's idempotent status update.

## Auth

JWT access (short-lived) + refresh (long-lived) tokens — stateless, least coupling for the Next.js
frontend (see `docs/architecture/devbutter-platform-backend.md`, "Stack"). `Account.is_admin` gates
the `/admin/*` routes; there's no separate admin table — Marcos is an `Account` with the flag set
(flip it directly in the database for the first admin; no self-serve promotion endpoint exists).

## What's not implemented here (see architecture doc §8–9 for open questions)

- Layer B (ecommerce client payments / Asaas BaaS subaccounts) — explicitly out of scope for this
  implementation.
- Automatic parsing of `Quote.price_range` into a billable amount — admin approval takes an explicit
  `amount` and `due_date` instead (see `orders/schemas.py::OrderApprovalRequest`); the range is a
  human-readable string, not something safe to parse into a charge.
- A self-serve way to promote an account to admin.
