Resolva o bloqueio estrutural descrito em `docs/auditoria-seguranca-2026-08-29.md` (item 1) no
relay de credenciais Selolibertaria, em `app/integrations/relay_service.py` e no wiring com
`AsaasClient`. Leia o documento inteiro antes de começar. O problema é bloqueante -- hoje toda
subconta fica `selolibertaria_relay_status="blocked"` em produção, tanto no provisionamento
inicial quanto em toda rotação.

## 1. Diagnóstico -- confirme a causa raiz antes de mexer em código

`app/integrations/relay_service.py:51-96` (`relay_initial_provisioning` e `relay_key_rotation`)
lê `access_token_response.get("apiKey")` e cai em `_mark_blocked` (linhas 106-112) sempre que essa
chave vem vazia. A causa é o `asaas_payment` (outro repositório/serviço) redigir `apiKey` em toda
resposta de `/subaccounts/{id}/access-tokens` (`hide_api_key=True`) -- inclusive na chamada que
`AsaasClient.create_access_token`/`list_access_tokens` faz aqui.

Confirme isso batendo diretamente no client (`app/core/clients/asaas_client.py`) contra o
ambiente de sandbox atual antes de prosseguir: se `asaas_payment` já foi corrigido para não
redigir `apiKey` nessa rota específica desde a auditoria, os itens 2-3 abaixo não fazem sentido --
pule direto para o item 4 (testes) confirmando que o fluxo desbloqueado funciona ponta a ponta.

## 2. Se `asaas_payment` ainda redige `apiKey` -- não corrija aqui, mas não trate como corrigido

Este repositório não tem escopo de escrita sobre `asaas_payment`. Não implemente workaround que
tente reconstruir ou obter o `apiKey` por outro caminho dentro deste serviço (ex.: chamar outro
endpoint do Asaas diretamente, ignorando o proxy) -- isso reintroduziria uma segunda fonte de
verdade para credenciais Asaas fora do proxy central, o que é exatamente o que o proxy existe para
evitar.

Em vez disso:

- Abra (ou confirme que já existe) o item correspondente no board/backlog do time responsável por
  `asaas_payment` pedindo que `/subaccounts/{id}/access-tokens` pare de redigir `apiKey` quando o
  caller for `devbutter_backend` autenticado via mTLS (o mesmo canal de confiança já usado para as
  demais chamadas administrativas desse client) -- ou que exponha uma rota dedicada que devolva o
  `apiKey` sem redigir, escopada a esse uso específico.
- Registre isso explicitamente no changelog (achado 7 da auditoria) e não remova o comentário de
  `_MISSING_API_KEY_PROVISIONING`/`_MISSING_API_KEY_ROTATION` em `relay_service.py:21-39` até a
  dependência ser resolvida do lado de lá.

## 3. Quando `asaas_payment` expuser o `apiKey` -- ajuste o consumo aqui

Quando a rota upstream deixar de redigir `apiKey` para este caller:

- Confirme que `AsaasClient.create_access_token`/`list_access_tokens` (`app/core/clients/asaas_client.py`)
  não filtra o campo por conta própria antes de devolver o dict -- se filtrar, remova esse filtro
  para este uso específico.
- `relay_service.py` não precisa mudar de lógica -- `if not api_key` deixa de disparar
  naturalmente assim que `apiKey` vier preenchido. Não remova o fallback `_mark_blocked`: ele
  continua sendo o comportamento correto para qualquer falha genuína de resposta (ex.: Asaas fora
  do ar), só deixa de ser o caminho permanente.
- Atualize os comentários das linhas 21-39 de `relay_service.py` para refletir que a redação foi
  resolvida e datar quando.

## 4. Testes

Depois de confirmar o desbloqueio (real ou simulado via fixture atualizada):

- Em `tests/test_selolibertaria_relay.py`, adicione um teste de `relay_initial_provisioning` e um
  de `relay_key_rotation` com `access_token_response` contendo `apiKey` preenchido, confirmando
  que `selolibertaria_relay_status` deixa de ser `"blocked"` e passa a refletir o resultado real do
  `_relay_client` (`sent`/`acked`/`failed`).
- Rode `scripts/run_proactive_rotation.py` manualmente contra o ambiente de sandbox atualizado e
  confirme que o `exit_code` deixa de ser 1 quando não há falha real de rede/upstream.
- Mantenha (não remova) os testes existentes que cobrem o caminho `blocked` com `apiKey` ausente --
  ele continua sendo um estado válido para falhas genuínas de resposta.

## Fora de escopo

Não implemente agendamento do script de rotação proativa aqui -- isso é tratado em
`docs/prompts/agendar-proactive-rotation.md`. Não mude o algoritmo de HMAC nem o contrato com a
Selolibertaria.

Ao terminar, rode `pytest`, `ruff check` e `mypy` antes de considerar concluído.
