# Implementar confirmação de e-mail no signup do portal

Hoje `AccountService.signup` (`app/accounts/service.py:64-67`) cria o `User` e já devolve um
`TokenPair` válido — a conta entra logada e utilizável sem qualquer verificação de que o e-mail
digitado existe/pertence a quem se cadastrou. `grep -rn "is_email_confirmed\|email_confirm\|verify_email" app/`
não retorna nada: não existe coluna, tabela de token, endpoint nem template de notificação para
isso. `PasswordSetupToken`/`PasswordRecoveryToken` (`app/accounts/models.py:159-198`) já dão o
padrão de "token de uso único hasheado" a seguir — não invente um mecanismo novo.

Siga a convenção de camadas do projeto (router fino → service com `@log_errors` → repository que
traduz falhas do SQLAlchemy → models) já usada em `app/accounts/*` e documentada em
`fastapi-builder`. Cada peça abaixo tem equivalente direto num arquivo existente — use-o como
molde.

## 0. Decisão de arquitetura (registrar como ADR curto no topo deste prompt ou em
   `docs/adr/NNNN-confirmacao-de-email.md`, seguindo o formato `engineering:architecture`)

Antes de codar, decida e documente estas duas questões — elas mudam o escopo do resto do prompt:

**Decisão 1 — o login bloqueia e-mail não confirmado?**
- Opção A (recomendada para começar): não bloquear. `is_email_confirmed=False` é só um sinal
  exibido no portal ("confirme seu e-mail") e usado depois para decisões que dependem de e-mail
  verificado (ex.: liberar redefinição de senha por e-mail só depois de confirmado). Menor
  fricção, consistente com o produto hoje aceitar `auth_method="magic_link"` sem senha.
- Opção B: `AccountService.login` (`app/accounts/service.py:70-78`) passa a rejeitar com
  `UnauthorizedError` quando `is_email_confirmed=False`. Mais seguro, mas quebra o fluxo de
  onboarding via chat (`get_or_create_from_extraction`, mesma classe) que cria contas sem
  interação síncrona do usuário — teria que reemitir o token de confirmação nesse caminho também.
- Registre a escolha e o motivo; o restante deste prompt assume a Opção A, mas os dois pontos de
  extensão (login e `get_or_create_from_extraction`) estão marcados abaixo com `[Opção B]` para
  quem decidir pela mais estrita.

**Decisão 2 — reenvio automático ou só sob demanda?**
- Recomendado: emitir o token de confirmação no próprio signup (síncrono, mesmo request) e expor
  um endpoint de reenvio para quando o e-mail expira ou some na caixa de spam. Não crie um job
  agendado para isso nesta rodada — está fora de escopo.

## 1. Migração e coluna em `User`

Em `app/accounts/models.py`, adicione a `User` (perto de `is_active`, linha ~27):

```python
is_email_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
```

Gere com `alembic revision --autogenerate -m "add users.is_email_confirmed"` — confirme que
`alembic/env.py` já importa `app.accounts.models` (deve importar, já que `PasswordRecoveryToken`
está no mesmo arquivo) para o autogenerate enxergar a mudança. Revise a migração gerada: o
`server_default="false"` evita backfill manual em linhas existentes, mas confirme que o
`downgrade()` remove a coluna corretamente.

## 2. Nova tabela `EmailConfirmationToken`

Em `app/accounts/models.py`, copie a forma de `PasswordSetupToken` (linhas 159-179) — mesmo
padrão de `token_hash` (nunca armazenar o token puro), `expires_at`, `used_at`:

```python
class EmailConfirmationToken(Base):
    """One-time link confirming a User's email address after signup --
    issued by AccountService.signup and re-issued by the resend endpoint.
    Separate table from PasswordSetupToken/PasswordRecoveryToken for the
    same reason those two are separate from each other: a token minted for
    one purpose must never be accepted by another purpose's endpoint.
    """

    __tablename__ = "email_confirmation_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

TTL sugerido: 24h (mais longo que os 45min do recovery, já que confirmar e-mail é menos sensível
que redefinir senha — mas ainda curto o bastante para não virar um link permanente esquecido).
Inclua a migração desta tabela na mesma revisão do item 1, ou numa segunda revisão encadeada —
qualquer uma é aceitável, não misture com migrações de outros domínios.

## 3. Repository (`app/accounts/repository.py`)

Siga exatamente o padrão de `create_password_setup_token` / `get_valid_password_setup_token` /
`mark_password_setup_token_used` (linhas 197-232), trocando o model:

- `create_email_confirmation_token(user_id) -> tuple[str, EmailConfirmationToken]` — ao emitir um
  novo, invalide os tokens ainda válidos desse usuário antes (mesmo padrão de
  `create_password_recovery_token`, linhas 234-259, que faz `UPDATE ... SET used_at=now()` nos
  tokens pendentes antes de criar o novo — evita que dois links de confirmação enviados em
  sequência fiquem ambos válidos).
- `get_valid_email_confirmation_token(raw_token) -> EmailConfirmationToken | None` — mesma
  normalização de timezone para SQLite mencionada no comentário de
  `get_valid_password_setup_token` (linhas 218-221).
- `confirm_email(token) -> User` — marca `used_at` e seta `user.is_email_confirmed = True` numa
  única transação (`async with self.session.begin()`, como em `consume_password_recovery_token`,
  linhas 280-283), não em dois `commit()` separados.

## 4. Service (`app/accounts/service.py`)

- `AccountService.signup`: depois de criar o usuário, se `self.notification_service is not None`,
  emita o token e chame `notification_service.notify_email_confirmation(user, raw_token)` — mesmo
  padrão de `try/except Exception: logger.exception(...)` usado em `request_password_recovery`
  (linhas 158-172): falha ao enfileirar a notificação nunca deve derrubar o signup.
- `resend_email_confirmation(user_id) -> None`: idempotente, não revela se o usuário já está
  confirmado (retorna silenciosamente se `is_email_confirmed` já é `True`, mesmo raciocínio de
  "não virar oráculo de existência de conta" do recovery).
- `confirm_email(raw_token) -> User`: chama o repository, levanta `NotFoundError` com mensagem
  genérica se o token for inválido/expirado/usado (mesma constante de mensagem no padrão de
  `_PASSWORD_RECOVERY_INVALID_DETAIL`, linha 35 — crie o equivalente para confirmação).
- `[Opção B]` se a Decisão 1 for pela opção estrita: adicione a checagem em `login` (linha ~72,
  junto dos outros `if user is None or not user.is_active...`) e, em
  `get_or_create_from_extraction`, decida se o usuário criado via chat nasce com
  `is_email_confirmed=True` (já que o e-mail veio de uma extração de IA sem verificação de posse)
  ou se precisa do mesmo fluxo de confirmação — documente a escolha, não deixe implícito.

## 5. Router (`app/accounts/router.py`)

Ao lado das rotas de password-recovery (linhas 190-223), adicione:

```python
@router.post(
    "/email-confirmation/resend",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resend the email-confirmation link",
)
async def resend_email_confirmation(
    payload: EmailConfirmationResend, service: AccountServiceDep
) -> None:
    await service.resend_email_confirmation_by_email(str(payload.email))


@router.post(
    "/email-confirmation/{token}",
    response_model=AccountRead,
    summary="Confirm an email address from a confirmation link",
)
async def confirm_email(token: str, service: AccountServiceDep) -> User:
    return await service.confirm_email(token)
```

Adicione `EmailConfirmationResend` (só `email: EmailStr`) a `app/accounts/schemas.py`, ao lado de
`PasswordRecoveryCheck`. Use `POST` (não `GET`) para confirmar, mesmo que o link do e-mail seja
clicável — o padrão do projeto (`/password-recovery/complete`) já faz o `GET` só checar e o
`POST` mutar; aqui não há uma etapa de "conferir antes" com dado sensível novo, mas mantenha o
verbo `POST` porque a operação muda estado (marca o token como usado e o usuário como confirmado)
e não deve ser cacheável/repetível por crawlers de e-mail que pré-carregam links do corpo da
mensagem (problema real e documentado em provedores de e-mail que fazem "link prefetching").

## 6. Notificação (`app/notifications/service.py`)

Copie o padrão de `notify_password_recovery` (linhas 90-101): novo `_TEMPLATE_EMAIL_CONFIRMATION`
e `notify_email_confirmation(user, token)` montando
`f"{settings.frontend_base_url}/portal/confirmar-email/{token}"`. Registre o template
`devbutter_email_confirmation` no admin do `butter_relay` (fora deste repositório) antes de subir
para produção — sem isso `RelayClient.send` vai falhar silenciosamente em enfileirar (mesmo
comportamento hoje para os outros templates).

## 7. Rate limiting

O mesmo argumento de `docs/prompts/rate-limit-password-recovery.md` vale aqui:
`/accounts/email-confirmation/resend` dispara efeito colateral (e-mail) e pode ser usado para
enumerar contas por timing — se aquele prompt já foi implementado, inclua esta rota na mesma
lista de rotas limitadas (`@limiter.limit("5/minute")`); se não foi, mencione a dependência no PR
mas não bloqueie este prompt por causa dela.

## 8. Testes

Siga o padrão de `tests/test_accounts_router.py` (ou equivalente):

- signup emite um `EmailConfirmationToken` e chama `notify_email_confirmation` (mock do
  `NotificationService`).
- `GET`/`POST /accounts/email-confirmation/{token}` com token válido marca `is_email_confirmed`
  e `used_at`; token expirado, já usado, ou inexistente retorna 404 com a mesma mensagem genérica.
- confirmar duas vezes com o mesmo token: segunda tentativa falha (token já consumido).
- `resend` é idempotente para usuário já confirmado (não cria token novo, não notifica de novo) —
  teste isso explicitamente, é o caso mais fácil de esquecer.
- se a Decisão 1 foi pela Opção B, adicione o teste de `login` rejeitando usuário não confirmado.

Ao terminar, rode `pytest`, `ruff check` e `mypy` antes de considerar concluído.
