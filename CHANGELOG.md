# Changelog

## [Unreleased]

- Adicionada a solicitação de recuperação de senha do portal: token aleatório
  armazenado somente como hash, validade de 45 minutos, invalidação de tokens
  anteriores e publicação no `butter_relay`, sem revelar contas inexistentes,
  inativas ou administrativas.
- Corrigidos os achados N-04/N-05 da validação adversarial (fatia 11):
  `PaymentCreate` (`POST /payments`) agora aceita adicionalmente `camelCase`
  (`invoiceId`/`paymentMethodId`/`idempotencyKey`) além do `snake_case` que
  o frontend sempre enviou, e contrato-07 seção 5.1 foi emendado para
  documentar `snake_case` como o formato real dessa rota (fora do protocolo
  de envelope, ver seção 0). Novos erros de domínio tipados em
  `app/core/exceptions.py` -- `InvoiceNotFoundError` (404
  `invoice_not_found`), `InvoiceNotPayableError` (409 `invoice_not_payable`,
  também fecha uma checagem de status de invoice paga/cancelada que faltava
  em `_owned_invoice`), `PaymentMethodNotFoundError` (404
  `payment_method_not_found`) e `IdempotencyConflictError` (409
  `idempotency_conflict`) -- substituem os `NotFoundError`/`ConflictError`
  genéricos que `POST /payments` e `DELETE /payment-methods/{id}` devolviam
  como `{"detail": "..."}` em vez do código estável do contrato-07 seção
  5.3. Ver docs/prompts/payment-tokenization/runs/13-fix-error-contract.md.
- Substituído o placeholder de `PaymentMethod` (Fase 2) pela chamada real a
  `asaas_payment` (`POST /internal/payment-methods/tokenize` -> Asaas
  `POST /creditCard/tokenizeCreditCard`, confirmado contra o sandbox real):
  `PaymentMethodService` agora tokeniza o cartão, resolve/cria o
  `AsaasCustomerLink` sob demanda, persiste `brand`/`last4`/`expiry` reais e o
  token do provedor cifrado com o Fernet já configurado
  (`payment_token_encryption_key`/`kid`), nunca mais `"pending"`/`"0000"`/
  `dev-placeholder-token:...`. `purpose=payment_method.create_and_pay` reusa
  `pay()` para cobrar `invoice.asaas_invoice_id` com o token recém-criado.
  Novo schema de plaintext `CardPayload`/`CardInput`/`CardHolderInfoInput`
  (`app/payment_methods/schemas.py`) com os nomes de campo reais do Asaas.
  Nova `PaymentProviderUnavailableError` (502 `payment_provider_unavailable`)
  para falha do Asaas/`asaas_payment` durante tokenização. Webhook Asaas
  agora também reconcilia `PaymentAttempt` para `succeeded` quando a fatura
  do cartão salvo é confirmada de forma assíncrona. Ver
  docs/prompts/payment-tokenization/runs/09-asaas-adapter-implementation.md.
- Realinhado `payment-methods/session` e `payment-methods` ao contrato congelado
  (docs/prompts/payment-tokenization/runs/07-contract-freeze.md): `purpose`
  (`payment_method.create`/`payment_method.create_and_pay`) substitui `operation`,
  `POST /payment-methods` recebe exatamente o envelope v1 sem wrapper, e o ledger
  `payment_security_contexts` ganha `kid`, `allowed_method`/`allowed_path`,
  `amount_minor`/`currency`, `failure_reason_code` e a transição explícita
  `unused -> processing -> consumed` (antes ia direto `unused -> consumed`).
  Ownership é sempre resolvida a partir da sessão autenticada, nunca do envelope;
  falha de contexto/ownership/replay/decriptação retorna uniformemente
  `400 invalid_secure_payload`. Tokenização Asaas real e mapeamento de campos de
  cartão permanecem stub, adiados para a Fase 3.
- Normalizado o contrato de métodos de pagamento para `invoice_id` e operações de tokenização
  `save_only`/`save_and_pay`; cobrança de cartão salvo permanece em `/payments`.
- Implementado fluxo de métodos de pagamento: BFF opaco, contexto anti-replay de uso único, autorização por fatura/conta, token Asaas cifrado, revogação, idempotência e reconciliação de timeout.

- Adicionada boundary server-side para descriptografar envelopes de pagamento apenas em memória, com keyring injetado, finalidade/contexto obrigatório e metadados seguros para logs.

- Adicionada a base de envelope seguro AES-256-GCM v1 para payloads sensíveis, com validação e testes locais.

- Expostos consumo e orçamento mensal de LLM no BFF administrativo da Mind.

- Adicionada ponte administrativa autenticada para métricas e limites operacionais do
  `butter_mind`, sem expor a chave interna ao frontend.
- Adicionada prévia segura do diagnóstico, persistência para retomada e bloqueio server-side de
  `draft-quote` antes de `ready_to_submit`.
- Separada a identidade em `users`; dados pessoais e credenciais não ficam mais em `accounts`.
- Adicionada a entidade `companies`: CNPJ e vínculo Asaas pertencem à empresa; cada empresa possui
  múltiplas contas/produtos e cada usuário pode selecionar qualquer uma delas pelo token ativo.
- Implementadas as rotas administrativas de clientes, pedidos, faturas, dashboard, catálogo,
  conversas, cotações e knowledge entries.
- Adicionado endpoint autenticado de faturas da conta e auditoria/reprocessamento de webhooks Asaas.
