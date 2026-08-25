# Changelog

## [Unreleased]

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
