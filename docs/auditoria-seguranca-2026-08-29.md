# Auditoria de segurança -- diff não commitado (2026-08-29)

Revisão do diff atualmente não commitado do devbutter_backend: recuperação de senha, segurança de métodos de pagamento, assinatura HMAC de webhook e a nova integração de relay/rotação de credenciais com a Selolibertaria. Descreve o código presente nesta revisão, não certifica execução integrada, Sandbox ou produção.

## 1. [SEGURANÇA -- Alta] Relay de credenciais Selolibertaria é inerte por construção

`app/integrations/relay_service.py` (`relay_initial_provisioning`, linhas 51-71, e `relay_key_rotation`, linhas 73-96) lê `access_token_response.get("apiKey")` e, se vazio, chama `_mark_blocked` (linhas 106-112), gravando `selolibertaria_relay_status="blocked"`.

O problema é upstream: `asaas_payment` redige `apiKey` (`hide_api_key=True`) em toda resposta de `/subaccounts/{id}/access-tokens`, inclusive a chamada que `devbutter_backend` faz via `AsaasClient.create_access_token`/`list_access_tokens`. Isso é documentado no próprio código (`app/integrations/relay_service.py:21-39`, comentário e constantes `_MISSING_API_KEY_PROVISIONING`/`_MISSING_API_KEY_ROTATION`).

Consequência prática: **toda** subconta ficará em `selolibertaria_relay_status="blocked"` em produção, tanto no provisionamento inicial quanto em toda rotação (reativa via `app/integrations/router.py::rotate_key` e proativa via `scripts/run_proactive_rotation.py`). O script de rotação proativa sempre reportará blocked e sairá com código 1 (`scripts/run_proactive_rotation.py:78-82`), o que vai gerar alerta permanente em qualquer scheduler externo que o invoque.

A feature está corretamente instrumentada (bookkeeping, HMAC, testes) mas não pode funcionar fim a fim até que `asaas_payment` pare de redigir `apiKey` para esta chamada específica, ou até que outra fonte do api_key seja definida. Isso está fora do escopo de leitura/gravação deste repositório (é outro serviço), mas bloqueia a funcionalidade inteira -- por isso é Alta e não um mero "known issue".

Prompt de correção: `docs/prompts/resolver-bloqueio-relay-selolibertaria.md`.

## 2. [SEGURANÇA -- Média] Idempotência check-then-create em `rotate_key` tem race condition

`app/integrations/router.py::rotate_key` (linhas 45-101): quando `event_id` está presente, o fluxo é `get_by_event_id` (linha 82) seguido de `create(..., event_id=event_id)` (linhas 93-98) -- duas operações separadas, sem lock nem transação que as amarre. Duas requisições concorrentes com o mesmo `event_id` (reenvio/retry da Selolibertaria) podem ambas passar pelo `get_by_event_id` antes que a primeira `create` seja commitada.

`WebhookEvent.event_id` tem `unique=True` no modelo (`app/webhooks/models.py:27`), então a segunda `create()` de fato viola a constraint no banco -- mas `WebhookEventRepository.create` (`app/webhooks/repository.py:15-35`) não captura `IntegrityError`, então a violação sobe crua como erro 500 não tratado, em vez de responder de forma idempotente (o padrão já usado em outros repositórios deste mesmo código, ver `app/orders/repository.py:27-35` e `app/payment_methods/repository.py:130-142`, que convertem `IntegrityError` em `ConflictError`/`IdempotencyConflictError`).

Adicionalmente: quando `event_id` está ausente no corpo (`body.get("event_id")` retorna `None`, linha 77), não há dedupe nenhum e nenhuma linha de auditoria é criada (o bloco `if event_id:` nas linhas 92-98 é pulado inteiro) -- a rotação real ainda roda (`rotation_service.rotate`, linha 100), mas sem qualquer registro em `webhook_events`.

Prompt de correção: `docs/prompts/idempotencia-rotate-key.md`.

## 3. [SEGURANÇA -- Média] Ausência de rate limiting em toda a aplicação

`grep -rn "rate_limit\|slowapi\|limiter\|Limiter" app/` não retorna nenhuma ocorrência, e `pyproject.toml` não lista `slowapi` nem dependência equivalente. `app/settings/middleware.py` só registra `CORSMiddleware` e `RequestContextMiddleware` (log/request-id) -- nenhum throttle.

Especificamente, `POST /accounts/password-recovery` (`app/accounts/router.py:190-199`, handler `request_password_recovery`) não tem nenhum limite de taxa por IP ou por e-mail. O serviço em si é seguro contra enumeração (resposta genérica `PasswordRecoveryRequestAccepted`, token CSPRNG de uso único e hash persistido -- ver `docs/devbutter-backend-fluxos.md`, seção 1) e não vaza se o e-mail existe, mas nada impede um flood de requisições: custo de CPU (hash de token), volume de e-mails disparados via Relay/SQS, e uma superfície de negação de serviço sobre essa rota e sobre `/accounts/login` e `/accounts/signup`, que têm o mesmo problema.

Prompt de correção (focado na rota citada no achado, generalizável): `docs/prompts/rate-limit-password-recovery.md`.

## 4. [SEGURANÇA -- Baixa / Ponto positivo] Assinatura HMAC de webhook está sólida

`app/webhooks/router.py:87-92` usa `hmac.compare_digest` para o token estático do Asaas (`receive_asaas_webhook`), substituindo uma comparação `!=` anterior (comentário nas linhas 89-91 referencia a correção). `app/integrations/hmac_signing.py` implementa HMAC-SHA256 com `hmac.compare_digest` (linha 75), janela de 5 minutos de *clock skew* (`MAX_CLOCK_SKEW_SECONDS = 300`, linha 25, checada na linha 73) e nonce por requisição (`sign`, linhas 40-49).

Cobertura de teste em `tests/test_hmac_signing.py`: round-trip (linha 10), corpo adulterado (linha 24), segredo errado (linha 37), timestamp obsoleto (linha 50), headers ausentes (linha 64), segredo vazio (linha 79) e frescor de timestamp/nonce por chamada (linha 92). Sem ação necessária.

## 5. [ITEM ABERTO -- Média, parcialmente resolvido desde a auditoria original] Cobertura de teste E2E do endpoint de rotação

A auditoria original apontava que nenhum teste exercitava `POST /integrations/selolibertaria/subaccounts/{id}/rotate-key` fim a fim. Isso mudou: `tests/test_integrations_router.py` (criado após a auditoria original) já cobre com headers reais assinados via `hmac_signing.sign`:

- caminho feliz com rotação de fato (linha 75-105);
- assinatura inválida -> 401 (linha 108-132);
- subconta desconhecida -> 404 (linha 135-155);
- mesmo `event_id` reenviado sequencialmente -> no-op idempotente (linha 158-188).

O que **ainda não é testado**, e é exatamente o que o achado 2 descreve:

- a corrida de concorrência real (duas requisições simultâneas com o mesmo `event_id`, via `asyncio.gather` batendo direto no endpoint ou no repository) -- o teste de idempotência existente é sequencial (`first` só então `second`, linhas 172-184), não prova ausência de race;
- o caminho sem `event_id` no corpo (dedupe ausente, nenhuma linha em `webhook_events`) não tem teste dedicado.

Prompt de correção: `docs/prompts/teste-e2e-rotate-key-endpoint.md` (ajustado para cobrir a lacuna real remanescente, não o endpoint como um todo, que já tem suíte).

## 6. [ITEM ABERTO -- Média] `scripts/run_proactive_rotation.py` não está agendado

O próprio docstring do script (`scripts/run_proactive_rotation.py:1-19`) declara que ele depende de um scheduler externo (cron, Kubernetes CronJob, ECS scheduled task) e que nenhum scheduler in-process (ex.: APScheduler) é dependência deste repositório -- confirmado: `pyproject.toml` não lista `apscheduler` nem equivalente, e não há nenhum arquivo de definição de CronJob/crontab neste repositório. Hoje o script é código morto até que a equipe de operação configure essa invocação externamente.

Combinado com o achado 1 (todo link ficará `blocked`), mesmo se agendado o script hoje sempre terminaria com `exit_code = 1` (linhas 78-82).

Prompt de correção: `docs/prompts/agendar-proactive-rotation.md`.

## 7. [INCONSISTÊNCIA -- Baixa] CHANGELOG.md não menciona o subsistema de relay/rotação

`git diff CHANGELOG.md` mostra apenas uma entrada sobre a documentação estática (`docs/devbutter-backend-fluxos.md`/`.mmd`) e uma sobre recuperação de senha. Não há nenhuma linha cobrindo a nova integração `app/integrations/` (relay_service, rotation_service, hmac_signing, router, client, script de rotação proativa, migration `20260828_1000_add_selolibertaria_relay_fields`). Recomenda-se adicionar uma entrada ao `[Unreleased]` descrevendo a feature e citando explicitamente seu estado bloqueado (achado 1) até que a redação do `apiKey` seja resolvida.

Sem prompt de implementação dedicado -- é edição direta de uma linha do changelog, não código.

## Pontos verificados limpos (positivos)

- **Admin/companies sem bypass de autenticação**: todos os endpoints de admin e de empresas exigem `CurrentAdminAccount`; não há rota administrativa acessível sem essa dependência.
- **Nenhum dado de cartão bruto (PAN/CVV) é manuseado ou persistido** em nenhum ponto do código revisado -- apenas tokens emitidos pelo Asaas trafegam, consistente com o fluxo descrito em `docs/devbutter-backend-fluxos.md` seção 4.
- **Nenhum segredo hardcoded** em `.env.example` ou `app/settings/config.py`; `DEVBUTTER_SELOLIBERTARIA_RELAY_HMAC_SECRET` é distribuído em branco por padrão, exigindo configuração explícita por ambiente (e `hmac_signing.verify` rejeita segredo vazio -- `tests/test_hmac_signing.py:79-89`).
- **Sem SQL cru**: os arquivos `repository.py` revisados usam exclusivamente `select`/`update`/`insert` do SQLAlchemy com parâmetros vinculados; nenhuma interpolação de string em query.
- **`schemas.py` das novas features são somente leitura** (campos de relay/rotação expostos em respostas), sem risco de mass-assignment via payload de entrada.
- **Migration Alembic consistente**: `20260828_1000_add_selolibertaria_relay_fields.py` corresponde campo a campo com `app/accounts/models.py` (`AsaasSubaccountLink.selolibertaria_relay_*`, `access_token_id`, `key_version`) e `app/webhooks/models.py` (`WebhookEvent.event_id`), com `downgrade()` simétrico ao `upgrade()`.
