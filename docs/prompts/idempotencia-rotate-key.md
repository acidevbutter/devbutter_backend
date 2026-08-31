Corrija a race condition de idempotência descrita em `docs/auditoria-seguranca-2026-08-29.md`
(item 2) em `app/integrations/router.py::rotate_key` e `app/webhooks/repository.py`. Leia o
documento inteiro antes de começar.

## 1. Diagnóstico

`app/integrations/router.py::rotate_key` (linhas 45-101) faz idempotência via
check-then-create: `event_repository.get_by_event_id(event_id)` (linha 82) seguido, se não achar
nada, de `event_repository.create(..., event_id=event_id)` (linhas 93-98) -- duas chamadas
separadas ao banco, sem lock. Duas requisições concorrentes com o mesmo `event_id` (reenvio da
Selolibertaria) podem ambas passar pelo `get_by_event_id` antes que a primeira `create` seja
commitada, e a segunda `create()` estoura `IntegrityError` (a coluna `WebhookEvent.event_id` já é
`unique=True`, `app/webhooks/models.py:27`) que hoje sobe crua como 500, em vez de responder de
forma idempotente.

Siga o padrão já usado neste código para esse exato problema em `app/orders/repository.py:27-35`
e `app/payment_methods/repository.py:130-142`: capturar `IntegrityError` no repository e traduzir
para uma exceção de domínio.

## 2. `WebhookEventRepository.create` -- capture a violação de unicidade

Em `app/webhooks/repository.py`, no método `create` (linhas 15-35 hoje):

- Envolva o `session.add`/`commit` em `try/except IntegrityError`, faça `await
  self.session.rollback()` no except, e levante `ConflictError` (`app.core.exceptions`) com uma
  mensagem tipo `f"Webhook event {event_id!r} already recorded"` -- não crie uma exceção nova, já
  existe `ConflictError` no domínio (`app/core/exceptions.py:32`, já mapeada para uma resposta
  HTTP via `@exception_handler(ConflictError)`, linha 118-119).
- Importe `from sqlalchemy.exc import IntegrityError` no topo do arquivo.

## 3. `rotate_key` -- trate o `ConflictError` como o mesmo no-op idempotente

Em `app/integrations/router.py::rotate_key`:

- Envolva a chamada a `event_repository.create(...)` (linhas 93-98) em `try/except
  ConflictError`. No except, retorne `{"received": True}` exatamente como o caminho de
  `get_by_event_id` encontrando um registro existente (linha 85) -- o efeito observável para o
  chamador deve ser idêntico, seja a checagem prévia que pegou o duplicado, seja a constraint do
  banco.
- Não remova a checagem `get_by_event_id` prévia (linha 82) -- ela continua sendo o caminho comum
  (sem race) e evita uma ida a mais ao banco/exceção no caso normal; a captura de `ConflictError`
  é apenas a rede de segurança para a janela de corrida.
- **Importante**: a chamada a `rotation_service.rotate(link, reason="reactive")` (linha 100) só
  deve rodar depois que a linha de auditoria for gravada com sucesso (create sem exceção, ou
  get_by_event_id sem achar nada e create ok) -- confirme que a ordem atual (create antes de
  rotate) se mantém, para não rodar a rotação real duas vezes mesmo que o registro de auditoria
  tenha sido deduplicado corretamente.

## 4. Caminho sem `event_id` -- decida e documente o comportamento

Hoje, quando `event_id` está ausente no corpo (`body.get("event_id")` retorna `None`, linha 77), o
bloco inteiro de dedupe/auditoria (linhas 92-98) é pulado e a rotação roda sem qualquer registro em
`webhook_events`. Corrija para que **toda** chamada a `rotate_key`, com ou sem `event_id`, deixe um
registro em `webhook_events` (use um `event_id` gerado localmente com `uuid.uuid4()` quando o
corpo não trouxer um, do mesmo jeito que `relay_service.py` já gera `event_id` para as chamadas
outbound). Isso fecha a lacuna de auditoria sem exigir que a Selolibertaria sempre mande
`event_id` (o contrato aparentemente permite omiti-lo).

## 5. Testes

Em `tests/test_integrations_router.py` (já existe e cobre o caminho feliz, 401 e no-op sequencial
com o mesmo `event_id` -- ver linhas 158-188 para o padrão a seguir):

- Adicione um teste de concorrência real: dispare duas requisições com o mesmo `event_id` via
  `asyncio.gather` batendo direto no endpoint (`client.post(...)` duas vezes em paralelo) e
  confirme que exatamente uma linha é criada em `webhook_events` para aquele `event_id`, ambas as
  respostas HTTP retornam 202 com `{"received": True}`, e `fake_asaas.create_access_token_calls`
  tem no máximo... [decida e documente aqui se o objetivo é "a rotação real roda uma única vez" --
  se sim, esse teste também prova isso, não só a ausência de 500].
- Adicione um teste unitário de `WebhookEventRepository.create` chamando `create` duas vezes com o
  mesmo `event_id` sequencialmente (sem passar pelo router) e confirmando que a segunda chamada
  levanta `ConflictError`, não `IntegrityError` crua.
- Adicione um teste do caminho sem `event_id` no corpo confirmando que agora existe uma linha em
  `webhook_events` mesmo assim.

Ao terminar, rode `pytest`, `ruff check` e `mypy` antes de considerar concluído.
