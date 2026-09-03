**Só execute depois que `selolibertaria/backend` estiver 100% migrado para o
`AsaasProxyClient` (ver prompt correspondente nesse repo) — este prompt remove o caminho
antigo, não convive com ele.**

Remova o relay HMAC de credencial (`POST /internal/asaas/credentials` e o job de rotação
proativa que empurra a `apiKey` em claro), conforme
`docs/architecture/adr-0002-asaas-subconta-proxy-nota-fiscal.md` (item 1) e o plano de
migração combinado anteriormente (Plano A/B).

## 1. Leia antes de começar

- Confirme com o time/checklist do `selolibertaria` que a migração para
  `AsaasProxyClient` já está em produção há tempo suficiente (ver critério de canário do
  plano de migração) antes de tocar em qualquer coisa aqui — esta é uma remoção
  destrutiva de um caminho que outros sistemas podem ainda depender.
- Localize a rota de relay e o job de rotação proativa (grep por "relay", "rotation",
  "HMAC" em `app/`) — este repo tem múltiplos módulos de rotação (ver
  `docs/prompts/agendar-proactive-rotation.md`,
  `docs/prompts/idempotencia-rotate-key.md`,
  `docs/prompts/resolver-bloqueio-relay-selolibertaria.md` para contexto histórico de
  como isso foi construído).

## 2. Entregáveis

- Remover a rota de emissão/relay de credencial para o `selolibertaria` especificamente
  (não mexer em relay para outros consumidores, se existirem).
- Remover o job de rotação proativa associado a esse relay.
- Atualizar a tela admin de Asaas (`devbutter_frontend/.../config/asaas`) para consultar
  `asaas_payment` (ou o `AsaasSubaccountLink` local, somente leitura) em vez de qualquer
  dado de chave.
- Atualizar `docs/prompts/asaas-payment-integration/CONTRATO-HMAC.md` (ou equivalente)
  marcando o contrato como descontinuado, sem apagar o histórico.

## 3. Regras

- Não apagar `AsaasSubaccountLink` nem nenhuma referência não sensível — só o caminho de
  transporte de segredo.
- Se qualquer teste existente depender do relay antigo, ajuste o teste para refletir o
  novo fluxo em vez de deletar a cobertura.

## 4. Testes

Rodar a suíte completa depois da remoção — o objetivo é garantir que nada mais no
`devbutter_backend` dependia silenciosamente dessa rota. `pytest`, `ruff check`, `mypy`.
