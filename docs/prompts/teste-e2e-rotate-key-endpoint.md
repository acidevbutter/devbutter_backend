Feche a lacuna de cobertura de teste remanescente em
`POST /integrations/selolibertaria/subaccounts/{id}/rotate-key`, descrita em
`docs/auditoria-seguranca-2026-08-29.md` (item 5). Leia o documento inteiro antes de começar.

**Importante**: `tests/test_integrations_router.py` já existe e já cobre o caminho feliz (linhas
75-105), assinatura inválida -> 401 (linhas 108-132), subconta desconhecida -> 404 (linhas
135-155) e mesmo `event_id` reenviado *sequencialmente* -> no-op (linhas 158-188). Não recrie
esses testes. Este prompt cobre só o que falta: a corrida de concorrência real e o caminho sem
`event_id`.

Se `docs/prompts/idempotencia-rotate-key.md` ainda não foi implementado, implemente-o primeiro --
os testes abaixo dependem do fix de idempotência (captura de `IntegrityError`/`ConflictError` em
`WebhookEventRepository.create`) para passar de forma determinística; sem ele, o teste de
concorrência vai falhar com um 500 cru em vez de provar o comportamento correto.

## 1. Teste de concorrência real com o mesmo `event_id`

Em `tests/test_integrations_router.py`, adicione um teste que dispare duas requisições
*simultâneas* (não sequenciais) para o mesmo `asaas_account_id` com o mesmo `event_id`, usando
`asyncio.gather`:

```python
async def test_reactive_rotate_key_concurrent_same_event_id_is_deduped(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    ...
    event_id = str(uuid.uuid4())
    body = json.dumps({"event_id": event_id, "reason": "unauthorized"}).encode()

    responses = await asyncio.gather(
        client.post(url, content=body, headers=_signed_headers(body)),
        client.post(url, content=body, headers=_signed_headers(body)),
    )

    assert all(r.status_code == 202 for r in responses)
    assert all(r.json() == {"received": True} for r in responses)
    # exatamente uma linha de auditoria e uma rotação real efetiva
    ...
```

Siga as fixtures/helpers já definidos no arquivo (`_create_link`, `_signed_headers`,
`_FakeAsaasClient`, `_FakeSelolibertariaClient`) -- não duplique esse setup. Verifique, via query
direta na sessão de teste (`db_session`) ou via um repository, que existe exatamente uma linha em
`webhook_events` para aquele `event_id` depois de ambas as respostas retornarem. Se o `client`/`db_session`
de teste usarem SQLite em memória (`aiosqlite`, presente em `pyproject.toml`) com um único
connection pool compartilhado entre as duas corrotinas, a corrida pode não ser observável da mesma
forma que em Postgres -- se for esse o caso, documente essa limitação no próprio teste com um
comentário, e complemente com o teste unitário de repository do item 2, que prova a proteção no
nível certo independente do driver de teste.

## 2. Teste unitário do dedupe no nível do repository

Complementar ao item 1 (e já pedido em `docs/prompts/idempotencia-rotate-key.md`, item 5): um
teste direto de `WebhookEventRepository.create` chamando duas vezes com o mesmo `event_id` na
mesma sessão e confirmando `ConflictError` na segunda chamada, sem depender do endpoint HTTP nem
de concorrência real -- isso prova a garantia de unicidade no nível onde ela realmente é aplicada
(constraint do banco), independentemente de quão bem o teste de concorrência acima consiga
reproduzir a corrida de fato.

## 3. Teste do caminho sem `event_id`

Adicione um teste postando o corpo `{"reason": "unauthorized"}` (sem `event_id`) para
`/integrations/selolibertaria/subaccounts/{id}/rotate-key` com assinatura válida, e confirme:

- resposta 202 com `{"received": True}`;
- a rotação real acontece (`fake_asaas.create_access_token_calls` tem uma entrada);
- (depois do fix do item 4 de `idempotencia-rotate-key.md`) existe uma linha em `webhook_events`
  para essa chamada, com um `event_id` gerado internamente -- ou, se a decisão tomada naquele
  prompt foi diferente, ajuste esta asserção para refletir o comportamento efetivamente
  implementado.

## 4. Teste de parsing de header malformado

Adicione um teste com um dos headers HMAC presentes mas em formato inválido (ex.:
`X-Selolibertaria-Timestamp: "not-a-number"`) e confirme que a rota responde 401
`{"detail": "invalid_signature"}` (não 422 nem 500) -- `hmac_signing.verify` já trata esse caso
internamente (`try/except ValueError` nas linhas 69-72 de `app/integrations/hmac_signing.py`),
este teste apenas prova que o comportamento chega intacto até a resposta HTTP.

Ao terminar, rode `pytest`, `ruff check` e `mypy` antes de considerar concluído.
