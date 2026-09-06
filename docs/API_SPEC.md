# Platform API — especificação de navegação

**Dono:** estado de contas, cotações, pedidos, faturas, catálogo e espelho de
conversas. Não implementa cobrança, LLM ou entrega de e-mail. Contrato de
campos e respostas: `/openapi.json` da instância em execução.

## Acesso

- `Authorization: Bearer <access JWT>` nas rotas autenticadas; o token inclui a
  conta selecionada.
- `/admin/*` exige usuário administrador.
- `POST /webhooks/asaas` valida `asaas-access-token`; não usa JWT.
- Conversas podem começar anonimamente; os demais recursos são escopados à conta.

## Rotas por domínio

| Prefixo | Operações | Módulo a abrir |
| --- | --- | --- |
| `/accounts` | `signup`, `login`, `refresh`; recuperação e definição de senha; `me`; listar/criar contas e empresas; `select` | `app/accounts/` |
| `/conversations` | criar, consultar, enviar mensagem, selecionar opção, atualizar perfil e criar rascunho | `app/conversations/` |
| `/quotes` | listar/consultar próprias; criar manual | `app/quotes/` |
| `/orders`, `/orders/{id}/invoices`, `/invoices` | listar/consultar pedidos e faturas | `app/orders/`, `app/invoices/` |
| `/payment-methods`, `/payments` | sessão de envelope, criar/listar/revogar método e cobrar; reconciliação em `/admin/payment-methods/reconcile` | `app/payment_methods/` |
| `/service-catalog` | CRUD do catálogo da conta | `app/service_catalog/` |
| `/admin/knowledge-entries` | CRUD de contexto/preço | `app/knowledge/` |
| `/webhooks/asaas` | receber evento Asaas idempotente | `app/webhooks/` |
| `/admin/webhooks/events` | listar, detalhar e reprocessar eventos | `app/webhooks/` |
| `/admin/quotes`, `/admin/clients`, `/admin/orders`, `/admin/invoices`, `/admin/dashboard`, `/admin/catalog`, `/admin/conversations` | filas e visão operacional | `app/admin/router.py` |
| `/admin/mind/*` | proxy de overview, limites, uso e métricas do Mind | `app/admin/router.py` |
| `/admin/subaccounts`, `/admin/asaas/audit-logs` | administração Asaas via cliente mTLS | `app/admin/payments_router.py` |
| `/admin/companies` | empresas, recursos e estado administrativo | `app/admin/companies_router.py` |

`GET /health` prova processo; `GET /health/ready` também verifica banco.

## Integrações e limites

- Mind é chamado somente por `app/core/clients/butter_mind_client.py`; não dar
  acesso dele a Quote/Order/Invoice.
- Asaas é chamado somente por `app/core/clients/asaas_client.py` com mTLS; a
  chave do provedor não entra neste serviço.
- Relay é publicação SQS em `app/core/clients/relay_client.py`, não HTTP.
- Para pagamento, preserve o envelope opaco e os contratos em
  `docs/prompts/payment-tokenization/`; não registrar PAN/CVV/token.

## Onde mudar

Cada domínio segue `router.py` → `schemas.py` → `service.py` → `repository.py`
→ `models.py` → `dependencies.py`. Comece pela rota e schema; não faça busca
global. O ASGI de produção é `app.main:asgi_app` (Socket.IO + FastAPI).
