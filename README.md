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
devbutter_platform (this service) -- Postgres (users, companies, accounts, conversations, quotes, orders, invoices, catalog, knowledge, webhook_events)
        |            |             |
        | mTLS        | HTTP        | boto3 SQS
        v            v             v
  asaas_payment   butter_mind   butter_relay
```

## Modules

Each domain follows the same six-file shape (`router.py`, `schemas.py`, `models.py`, `service.py`,
`repository.py`, `dependencies.py` — the latter holds the domain's DI wiring: `get_*_service` /
`get_*_repository` factories, their `Annotated[..., Depends(...)]` aliases, and the auth deps the
domain owns, so `router.py` is routes only). When one domain needs another's DI it imports from
`app.<owner>.dependencies`, never from `app.<owner>.router`; `core/dependencies.py` keeps only
cross-domain wiring (`DbSession`). `admin/` and `notifications/` still deviate deliberately because
they are an aggregate view and an integration adapter (see
`docs/architecture/diagrams/backend-structure.mmd`):

| Module | Responsibility |
|---|---|
| `accounts` | users/credentials, companies, account products, selected-account JWT and the `AsaasCustomerLink` |
| `conversations` | wraps a `butter_mind` guided-diagnosis session; local transcript mirror |
| `quotes` | draft (`source`: `ai` \| `manual`), status (`draft`/`approved`/`edited`/`rejected`) |
| `orders` | created on quote approval; drives Asaas Customer + Invoice creation |
| `invoices` | local mirror of an Asaas cobrança, kept in sync via webhook |
| `webhooks` | public receiver plus durable audit log for Asaas payment-status events (`asaas-access-token`, idempotent) |
| `payment_methods` | one-time encrypted submission contexts, saved-card metadata and encrypted provider tokens |
| `service_catalog` | metadata a client registers about their own business/store |
| `knowledge` | admin-managed pricing/context entries used by the platform |
| `admin` | Marcos's review queue and cross-account operational views — not its own entity |
| `notifications` | builds the payload and publishes it onto `butter_relay`'s SQS queue |
| `core/clients` | the three integration clients — the only place this service talks to a sibling |

## Integration contracts

- **→ `asaas_payment`** (`core/clients/asaas_client.py`): mTLS client, reusing
  `asaas_payment/scripts/generate_dev_certs.sh`. Calls the generic pass-through routes
  (`POST /customers`, `POST /payments`, forwarded to Asaas's own `/v3/customers` and `/v3/payments`)
  because Layer A bills DevButter's own clients under DevButter's single Asaas account — it does not
  act through a white-label subaccount (Layer B, out of scope here; see the architecture doc §2).
  This service never sees `ASAAS_API_KEY`.
  Card tokenization and saved-card payment use the separate mTLS-only
  `/internal/payment-methods/*` boundary; its caller supplies only an opaque browser envelope.
- **→ `butter_mind`** (`core/clients/butter_mind_client.py`): plain HTTP over the shared Docker
  network (`mind-api`), driving butter_mind's `/diagnosis/sessions` flow. butter_mind produces the
  schema-validated draft (`problem_summary`, `services_of_interest`, `budget_range`, `timeline`);
  `quotes/service.py` turns that into a `Quote(source="ai")`. butter_mind has no write access to
  Quote/Order/Invoice. Admin-only `/admin/mind/*` routes proxy metrics, governance and budgets
  mensais por fluxo com `BUTTER_MIND_INTERNAL_API_KEY`; esta chave nunca chega ao frontend.
- **→ `butter_relay`** (`core/clients/relay_client.py`): `boto3` `SendMessage` onto the shared SQS
  queue, tagged with this service's `project_id`. Only builds `{template_key, to, payload}` —
  templates (`devbutter_quote_approved`, `devbutter_payment_status_changed`) must be registered in
  the `butter_relay` admin, not hardcoded here.
- **← Asaas webhook** (`webhooks/router.py`): `POST /webhooks/asaas`, validates the
  `asaas-access-token` header. Idempotent by construction — `InvoiceRepository.update_status()` is a
  no-op if the mapped status is already applied.

### Local development

Use `../scripts/dev-up.sh` from the workspace root. It creates the shared PostgreSQL and network,
starts the sibling services, and applies the local migrations.

## Local development

```bash
poetry install
cp .env.example .env
# Fill in JWT_SECRET, ASAAS_WEBHOOK_ACCESS_TOKEN, RELAY_PROJECT_ID.
# mTLS client cert for asaas_payment:
#   ../asaas_payment/scripts/generate_dev_certs.sh devbutter-platform
#   cp ../asaas_payment/certs/{devbutter-platform.crt,devbutter-platform.key,ca.crt} ./certs/
#   (then add "devbutter-platform" to asaas_payment's ALLOWED_CLIENT_CNS)
../scripts/dev-up.sh
# use ../scripts/dev-up.sh --build when images need rebuilding
```

The script creates the `devbutter_shared` network and shared PostgreSQL before starting the services.

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

Covers: signup/login/refresh and the email-uniqueness 409, multi-account selection and scoped
ownership checks, admin cross-account views, knowledge-entry CRUD and ownership/role checks, the
`log_call`/`log_errors` decorators (sync, async, error path), and the full
manual-quote → admin-approval → Order/Asaas-Customer/Invoice orchestration with `asaas_payment` and
`butter_relay` faked at the client boundary — including the 409 on re-approving an already-approved
quote, the Asaas webhook's idempotent status update, and webhook event listing/reprocessing.

## Auth

`users` stores email, password hash, name, CPF and phone. A `User` owns one or more `companies`; each
`Company` stores its name/CNPJ and owns one or more product `accounts`. Quotes, orders, invoices and
catalog entries remain account-scoped, while the `AsaasCustomerLink` is company-scoped. JWT access and
refresh tokens carry both the user and selected account; `GET /accounts` lists the selector options and
`POST /accounts/select` changes the active context. `User.is_admin` gates `/admin/*`; there is no separate
admin table (flip the flag directly in the database for the first admin).

## What's not implemented here (see architecture doc §8–9 for open questions)

- Layer B (ecommerce client payments / Asaas BaaS subaccounts) — explicitly out of scope for this
  implementation.
- Automatic parsing of `Quote.price_range` into a billable amount — admin approval takes an explicit
  `amount` and `due_date` instead (see `orders/schemas.py::OrderApprovalRequest`); the range is a
  human-readable string, not something safe to parse into a charge.
- A self-serve way to promote an account to admin.
