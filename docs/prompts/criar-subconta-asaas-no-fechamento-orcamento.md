Ao aprovar um orçamento (`Quote.status = approved`), crie a subconta Asaas do cliente via
`asaas_payment`, conforme `docs/architecture/adr-0002-asaas-subconta-proxy-nota-fiscal.md`
(item 1). Hoje esse trigger não existe — a única coisa que acontece na aprovação é a
criação do `AsaasCustomerLink` (Layer A, cobrança da própria DevButter) em
`app/orders/service.py`; isto aqui é aditivo, uma segunda coisa que passa a acontecer no
mesmo evento, para uma finalidade diferente (Layer B — o cliente vira lojista/recebedor).

## 1. Leia antes de começar

- `app/quotes/router.py`, `app/quotes/models.py`, `app/orders/service.py` — onde e como
  `Quote.status` vira `approved` hoje, e onde o `AsaasCustomerLink` já é criado (para
  replicar o estilo, não a finalidade).
- `app/accounts/models.py` (`Company`, `AsaasCustomerLink`) — mesmo padrão de referência
  não sensível a seguir para o novo `AsaasSubaccountLink`.
- `asaas_payment/app/subaccounts/schemas.py` (`SubaccountCreate`) — payload exato que a
  chamada precisa montar.
- Certifique-se de ter (ou pedir) o certificado mTLS de `devbutter_backend` como
  consumidor do `asaas_payment` antes de escrever o client — sem isso a chamada real não
  vai autenticar, só dá pra testar com mock.

## 2. Entregáveis

- Novo model `AsaasSubaccountLink(company_id, asaas_account_id, wallet_id, status,
  created_at, updated_at)` — **sem nenhum campo de chave/segredo**, mesmo padrão do
  `AsaasCustomerLink` existente.
- Client mTLS mínimo para chamar `asaas_payment` (`POST /subaccounts`) — pode ser o mesmo
  módulo que os próximos upgrades de split vão reusar; não duplique se um client HTTP
  mTLS genérico já fizer sentido em `app/core/clients/`.
- No fluxo de aprovação do orçamento: depois de `Quote.status = approved`, chamar
  `POST /subaccounts` no `asaas_payment` com os dados da `Company`, e persistir o
  `AsaasSubaccountLink` com o retorno.
- Trate falha dessa chamada como não bloqueante para a aprovação do orçamento em si
  (a aprovação já aconteceu; registre o erro e deixe um caminho de retry/reconciliação
  manual — não deixe o orçamento "meio aprovado" por causa de uma falha no Asaas).

## 3. Regras

- Não guardar nenhuma chave da subconta aqui — se em algum ponto da implementação você
  sentir necessidade de guardar a `apiKey`, pare: isso já foi decidido para viver só no
  `asaas_payment` (ADR-0002).
- Não remover ainda o relay HMAC de credencial existente — ele continua ativo até o
  `selolibertaria` migrar (ver prompt de descomissionamento separado).

## 4. Testes

Cobrir: aprovação de orçamento cria `AsaasSubaccountLink` corretamente; falha na chamada
ao `asaas_payment` não impede a aprovação nem corrompe o estado da `Quote`; idempotência
(reaprovar/reprocessar não cria uma segunda subconta para a mesma `Company`).

Ao terminar, rode `pytest`, `ruff check` e `mypy`.
