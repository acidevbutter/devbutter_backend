# Fluxos existentes do devbutter_backend

Revisão estática do checkout em 2026-08-27. Descreve o código presente, não certifica execução integrada, Sandbox ou produção.

## Visão geral

~~~mermaid
flowchart LR
    C[Cliente / frontend] --> A[FastAPI backend]
    A --> DB[(PostgreSQL)]
    A --> M[butter_mind HTTP/SSE]
    A --> R[butter_relay SQS]
    A --> P[asaas_payment mTLS]
    P --> AS[Asaas]
    AS -->|webhook com token| A
~~~

O backend concentra identidade, contas, empresas, catálogo, conversas locais, ações comerciais, faturas, métodos de pagamento e auditoria de webhook. IA, pagamento e e-mail permanecem em serviços externos.

## Fluxos observados

### 1. Identidade, seleção e recuperação

/accounts/signup cria usuário, empresa e conta e devolve JWT. Login e refresh validam usuário, conta selecionada e session_version. Access token é exigido nas rotas protegidas; user.is_admin libera /admin/*. O usuário pode listar/criar empresas e contas e trocar a conta ativa.

password-setup é onboarding separado. password-recovery gera token CSPRNG, persiste somente hash, invalida tokens anteriores, publica e-mail no Relay e, na conclusão, altera a senha e incrementa session_version na mesma transação.

Forças: ownership é resolvido no servidor; respostas de recuperação são genéricas; sessões antigas são invalidadas em REST, refresh e Socket.IO.

Pontos de atenção: não há rate limit visível neste repositório para login, signup ou recuperação; o fluxo depende de template/entrega do Relay; promoção administrativa é alteração direta de banco; defaults de configuração são adequados apenas para desenvolvimento e precisam de validação de ambiente.

### 2. Conversa de visitante e cotação por IA

POST /conversations cria sessão no butter_mind e espelha a conversa local. Mensagens seguem por HTTP ou Socket.IO/SSE, e o backend persiste transcript, ready_to_submit e draft_preview. Quando pronta, POST /conversations/{id}/draft-quote submete a sessão, obtém a extração estruturada, cria ou vincula conta pelo e-mail extraído, associa a conversa e cria Quote com source=ai.

O cliente pode conversar sem conta. Depois do vínculo, a conversa fica associada à conta; para conta já existente não há auto-login. Para conta nova, o backend pode emitir tokens e publicar confirmação/link de password-setup.

Forças: IA não grava diretamente em pedido/fatura; o draft final é reextraído no servidor; preço preliminar é ocultado das rotas do cliente antes da revisão; há espelho local para retomada e auditoria administrativa.

Falhas/lacunas: conversa visitante não reivindicada é acessível a quem possuir seu UUID; uma sessão Socket.IO já autorizada pode manter associação após a conversa ser vinculada; erro de streaming envia str(exc) ao cliente; não há limite de tamanho/frequência evidente para mensagens ou transcript; a cadeia de criação de conta, vínculo e cotação não é uma transação única.

### 3. Cotação, revisão e faturamento

Há dois ingressos: cotação manual de admin (POST /admin/quotes/manual) e cotação derivada da conversa. Ambas começam como draft. Admin pode editar, aprovar ou rejeitar. A aprovação cria Order, resolve/cria o cliente Asaas, cria cobrança, persiste Invoice, marca o pedido como invoiced e publica notificação.

Forças: revisão humana é uma barreira antes de cobrar; Quote tem estados e revisor; preço faturável é um amount explícito na aprovação, não uma tentativa de interpretar price_range; o cliente vê o valor aprovado da fatura.

Falhas/lacunas: aprovação, chamadas externas e persistência local não têm saga/outbox/idempotência visível; falha depois de criar Order, cliente ou cobrança pode deixar estado parcial e recursos órfãos; notificação síncrona pode falhar depois do commit e transformar sucesso de negócio em erro HTTP; não há fluxo implementado de conclusão/cancelamento de Order além dos estados declarados.

### 4. Fatura, cartão salvo e reconciliação

Faturas são espelho local da cobrança Asaas. Para cartão salvo, o cliente pede POST /payment-methods/session; o backend fixa conta, empresa, finalidade, fatura, valor e kid. Em seguida recebe o envelope v1 em POST /payment-methods, faz unused -> processing -> consumed, descriptografa somente em memória, tokeniza via mTLS, cifra o token do provedor e persiste apenas metadados permitidos. POST /payments cria PaymentAttempt com chave de idempotência e cobra o token salvo. Webhook ou /admin/payment-methods/reconcile pode confirmar tentativas indefinidas.

Forças: autorização/ownership não vêm do envelope; replay, contexto inválido e falha criptográfica têm resposta uniforme; PAN/CVV não são persistidos; token do provedor é cifrado; tentativa possui chave única por fatura.

Falhas/lacunas: reconciliação depende de chamada manual/admin, sem worker periódico visível; falhas entre Asaas e PaymentAttempt ainda exigem tratamento operacional; não há rate limit específico; remote_ip usa o socket observado pela aplicação, sem política de proxy confiável neste serviço; testes de rota substituem o cliente Asaas e não provam tokenização real.

### 5. Webhook Asaas

POST /webhooks/asaas valida asaas-access-token, grava o payload em webhook_events, traduz status Asaas para Invoice, reconcilia tentativas e publica notificação quando o status local muda. Admin lista eventos e reprocessa falhas.

Forças: evento aceito tem auditoria durável; há reprocessamento; atualização de status repetida é no-op; o evento pode reconciliar pagamento assíncrono.

Falhas/lacunas: autenticação usa segredo estático em header, sem assinatura, timestamp ou proteção contra replay visível; payload bruto é armazenado e a mensagem de erro pode conter detalhes de exceção; não há fila/dead-letter para processamento assíncrono; duas entregas concorrentes ainda merecem teste de concorrência e garantia de notificação única.

### 6. Catálogo e administração

/service-catalog é CRUD autenticado e escopado à conta. /admin oferece filas e visões paginadas de clientes, cotações, pedidos, faturas, catálogo, conversas e dashboard, além de governança/uso/orçamento do butter_mind. Subaccounts, split rules, tokens, pagamentos, refund/cancel e audit logs delegam em asaas_payment.

Forças: rotas administrativas têm gate comum; leituras paginam; cliente não acessa diretamente segredos de Asaas ou Mind; chamadas sensíveis passam pelos clients centralizados.

Falhas/lacunas: autorização administrativa é binária (is_admin), sem papéis ou escopo de ação; endpoints de pagamentos administrativos aceitam payloads como dict[str, Any]; a integração depende de configuração ADMIN_CLIENT_CNS no asaas_payment, fora deste repositório.

## Matriz de cobertura

| Domínio | Estado observado | Dependência externa | Principal atenção |
|---|---|---|---|
| Auth/contas | Implementado | PostgreSQL | rate limit, gestão de admin |
| Recuperação | Implementado | Relay/SQS | template, entrega e rate limit |
| Conversa IA | Implementado | butter_mind, Socket.IO | autorização persistente e limites |
| Cotação/revisão | Implementado | Relay | atomicidade e outbox |
| Pedido/fatura | Implementado | asaas_payment/Asaas | compensação e idempotência |
| Cartão salvo | Implementado | asaas_payment/Asaas | reconciliação automática e prova real |
| Webhook | Implementado | Asaas | assinatura, replay e concorrência |
| Catálogo/admin | Implementado | Mind/Asaas em partes | RBAC e schemas de entrada |

## Ordem recomendada de atenção

1. Definir saga/idempotência/outbox para aprovação, cobrança e notificações.
2. Revalidar ownership no Socket.IO em cada envio e limitar exposição do erro.
3. Endurecer webhook: assinatura/replay, payload redigido, fila e teste concorrente.
4. Automatizar reconciliação de pagamentos e explicitar readiness/mTLS/proxy.
5. Adicionar rate limits, RBAC granular e schemas tipados nos endpoints admin.

## Validação e limites

Esta revisão foi feita por leitura de routers, services, repositories, models, clients, configuração, Dockerfile e testes. Testes locais cobrem muitos fluxos com SQLite, mocks/stubs ou overrides de clientes. Isso não comprova PostgreSQL compartilhado, mTLS real, Relay/SQS real, butter_mind em execução, Sandbox/Asaas, concorrência sob carga, deploy ou produção.

