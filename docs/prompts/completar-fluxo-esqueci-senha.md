# Completar o fluxo "esqueci minha senha" ponta a ponta (BFF + link morto no login)

O backend do fluxo de recuperação de senha **já está implementado e correto** —
`AccountService.request_password_recovery` / `check_password_recovery` /
`complete_password_recovery` (`app/accounts/service.py:158-192`), as três rotas
`POST /accounts/password-recovery`, `GET /accounts/password-recovery/{token}` e
`POST /accounts/password-recovery/complete` (`app/accounts/router.py:190-223`), a tabela
`PasswordRecoveryToken` com hash de token + TTL de 45min + invalidação do token anterior ao emitir
um novo (`app/accounts/repository.py:234-259`), e o template `devbutter_password_recovery`
(`app/notifications/service.py:90-101`). Não recrie nada disso.

O que falta é o que consome isso: `grep -rn "password-recovery" devbutter_frontend/src` não
retorna nada — não existe rota Next.js (`/api/portal/auth/password-recovery*`) nem tela. O link
"Esqueci minha senha" em `devbutter_frontend/src/app/portal/login/page.tsx:150` é hoje um
`<div>` sem `onClick`/`href`, e-mail nenhum é enviado por causa disso. Este prompt fecha essa
lacuna no lado de integração (rotas BFF); a tela em si tem um prompt separado para o Claude
Design (`docs/prompts/claude-design-tela-esqueci-senha.md`, no repo do frontend) — os dois devem
ser lidos juntos, já que as rotas BFF aqui são o contrato que a tela vai consumir.

## 1. Rotas BFF (Next.js route handlers)

Siga exatamente o padrão de `src/app/api/portal/auth/password-setup/route.ts` e
`src/app/api/portal/auth/password-setup/[token]/route.ts` (mesma forma de request/response, só
troca o endpoint de destino) — não são fluxos diferentes o bastante para justificar uma
abordagem nova.

**`src/app/api/portal/auth/password-recovery/route.ts`** (`POST`, solicita o link):

```ts
import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/portal/config";

export async function POST(request: Request) {
  let email: unknown;
  try {
    ({ email } = await request.json());
  } catch {
    return NextResponse.json({ error: "invalid_body" }, { status: 400 });
  }
  if (typeof email !== "string" || !email) {
    return NextResponse.json({ error: "invalid_email" }, { status: 400 });
  }

  await fetch(`${API_BASE_URL}/accounts/password-recovery`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
    cache: "no-store",
  });

  // A rota do backend sempre responde 202 independente de o e-mail existir
  // (não é um oráculo de contas) -- o BFF preserva essa propriedade e nunca
  // repassa o status/corpo cru da resposta do backend aqui.
  return NextResponse.json({ ok: true });
}
```

**`src/app/api/portal/auth/password-recovery/[token]/route.ts`** (`GET`, checa o link antes de
renderizar o formulário) — cópia direta de `password-setup/[token]/route.ts`, trocando o path:

```ts
import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/portal/config";

export async function GET(_request: Request, { params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const checkRes = await fetch(`${API_BASE_URL}/accounts/password-recovery/${token}`, {
    cache: "no-store",
  });
  if (!checkRes.ok) {
    return NextResponse.json({ error: "invalid_or_expired" }, { status: checkRes.status });
  }
  return NextResponse.json({ ok: true });
}
```

Note que `GET /accounts/password-recovery/{token}` no backend devolve `PasswordRecoveryCheck`
(hoje um schema vazio, `app/accounts/service.py:174-181` só confirma validade) — diferente do
`password-setup/{token}` que devolve `{ email }`. Não invente um campo `email` aqui: o backend
deliberadamente não expõe o e-mail do dono do token nessa checagem (o link já foi enviado para o
e-mail certo; expor de volta não agrega nada e é uma superfície a menos para vazar). A tela deve
tratar a ausência desse campo como esperada.

**`src/app/api/portal/auth/password-recovery/complete/route.ts`** (`POST`, define a nova senha e
loga) — mesma forma de `password-setup/route.ts`, reaproveitando `setPortalSessionCookies`:

```ts
import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/portal/config";
import { setPortalSessionCookies } from "@/lib/portal/session";

export async function POST(request: Request) {
  let token: unknown;
  let password: unknown;
  try {
    ({ token, password } = await request.json());
  } catch {
    return NextResponse.json({ error: "invalid_body" }, { status: 400 });
  }
  if (typeof token !== "string" || typeof password !== "string" || password.length < 8) {
    return NextResponse.json({ error: "invalid_password" }, { status: 400 });
  }

  const res = await fetch(`${API_BASE_URL}/accounts/password-recovery/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, password }),
    cache: "no-store",
  });
  if (!res.ok) {
    return NextResponse.json({ error: "invalid_or_expired" }, { status: res.status });
  }

  const tokens = (await res.json()) as { access_token: string; refresh_token: string };
  await setPortalSessionCookies(tokens);
  return NextResponse.json({ ok: true });
}
```

Confirme o nome exato do body que `POST /accounts/password-recovery/complete` espera
(`CompletePasswordRecovery` ou equivalente em `app/accounts/schemas.py`) antes de fechar — o
exemplo acima assume `{ token, password }` pelo padrão do password-setup, mas valide contra o
schema real do endpoint.

## 2. Ligar o link morto no login

Em `src/app/portal/login/page.tsx:150`, troque:

```tsx
<div className={styles.forgot}>Esqueci minha senha</div>
```

por um link de fato para a tela de recuperação (a tela em si é objeto do prompt do Claude
Design). Se o `email` já digitado no formulário de login estiver preenchido, propague como query
param para pré-popular o campo de e-mail na próxima tela (`?email=...`), mas nunca dependa só
disso — o formulário de recuperação precisa funcionar também sem esse parâmetro (acesso direto à
URL).

```tsx
<a className={styles.forgot} href={`/portal/recuperar-senha${email ? `?email=${encodeURIComponent(email)}` : ""}`}>
  Esqueci minha senha
</a>
```

Isso implica que `.forgot` em `Login.module.scss` hoje está estilizado como texto estático — quando
o Claude Design gerar a tela, confira se o estilo de link (cursor, hover) precisa de ajuste
nessa classe também.

## 3. Dependência: rate limiting

`docs/prompts/rate-limit-password-recovery.md` (mesmo repositório) já cobre
`POST /accounts/password-recovery` no backend. Se ainda não foi implementado quando este prompt
for executado, isso não bloqueia as rotas BFF acima (o backend responde normalmente sem limite),
mas sinalize no PR que a rota fica exposta a flood/enumeração por IP até aquele prompt ser
aplicado — não duplique a implementação de rate limit aqui no lado do Next.js.

## 4. Testes

- Testes de rota (Vitest/Jest, o que já for usado no frontend para `route.ts`) para os três
  handlers: corpo inválido → 400; backend indisponível/token inválido → o BFF nunca vaza o corpo
  cru do erro do backend, só `{ error: "..." }`; sucesso do `complete` seta os cookies de sessão
  do portal (mesmo teste que provavelmente já existe para `password-setup`, adaptado).
- Teste manual: solicitar recuperação para um e-mail existente e um inexistente devem ter
  exatamente a mesma resposta HTTP do BFF (verificação da propriedade "não é oráculo de contas"
  ponta a ponta, não só no backend).

Ao terminar, rode a suíte de testes e o lint do frontend antes de considerar concluído.
